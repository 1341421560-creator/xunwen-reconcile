import json
import os
from contextlib import contextmanager
from pathlib import Path
from .audit import identifier, timestamp
from .ledger_model import new_ledger, validate_ledger
from .ledger_validation import LedgerCorruptionError
from .normalize import name_key
from .resource_lock import resource_mutex
from .atomic_write import replace_snapshot


class LedgerStore:
    def __init__(self, directory, temp_directory, company_name):
        self.directory = Path(directory)
        self.temp = Path(temp_directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.temp.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / "company-ledger.v1.json"
        self.lock_path = self.temp / "company-ledger.lock"
        self.company_name = company_name

    @contextmanager
    def exclusive(self):
        if os.name == "nt":
            with resource_mutex(self.path):
                yield
        else:
            with self.file_lock():
                yield

    @contextmanager
    def file_lock(self):
        # 锁文件长期保留，进程退出时由操作系统释放锁，避免残留锁误阻塞。
        with self.lock_path.open("a+b") as stream:
            stream.seek(0, 2)
            if stream.tell() == 0:
                stream.write(b"0")
                stream.flush()
            stream.seek(0)
            try:
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                raise ValueError("另一操作正在保存账本，请刷新后重试") from None
            try:
                yield
            finally:
                stream.seek(0)
                if os.name == "nt":
                    msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(stream.fileno(), fcntl.LOCK_UN)

    def load(self):
        if not self.path.exists():
            return new_ledger(self.company_name)
        try:
            ledger = json.loads(self.path.read_text(encoding="utf-8"))
            validate_ledger(ledger)
            if name_key(ledger["company"]["name"]) != name_key(self.company_name):
                raise ValueError("账本公司与当前配置不符")
        except (ValueError, TypeError, KeyError, AttributeError) as exc:
            raise LedgerCorruptionError("账本校验失败，已停止读写且未重建或覆盖原文件。请保留当前文件并核对备份。原因：" + str(exc)) from exc
        return ledger

    def commit(self, ledger, expected_revision):
        with self.exclusive():
            actual = self.load()["revision"]
            if type(expected_revision) is not int or actual != expected_revision:
                raise ValueError("账本已更新，当前页面版本过期。请刷新后重新核对并提交")
            ledger["revision"] = actual + 1
            ledger["saved_at"] = timestamp()
            validate_ledger(ledger)
            stage = self.temp / (identifier("snapshot-") + ".json")
            with stage.open("w", encoding="utf-8", newline="\n") as stream:
                json.dump(ledger, stream, ensure_ascii=False, separators=(",", ":"))
                stream.flush()
                os.fsync(stream.fileno())
            saved = json.loads(stage.read_text(encoding="utf-8"))
            validate_ledger(saved)
            if saved != ledger:
                raise ValueError("暂存账本校验失败，原账本未改动")
            # 暂存目录与正式账本须在同一磁盘；替换失败时原账本保持完整。
            replace_snapshot(stage, self.path)
        return ledger
