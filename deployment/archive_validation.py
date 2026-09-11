import hashlib
import json
import re
import stat
import zipfile
from pathlib import PurePosixPath
from reconcile.ledger_validation import validate_ledger
from reconcile.normalize import name_key

MAX_EXPANDED_BYTES = 512 * 1024 * 1024
MAX_FILES = 20000
LEDGER_PATH = "ledger/company-ledger.v1.json"
DATA_FOLDERS = ("ledger", "sessions", "reports")


def digest(content):
    return hashlib.sha256(content).hexdigest()


def check_member(name):
    path = PurePosixPath(name)
    if not name or "\\" in name or path.is_absolute() or path.as_posix() != name or any(part in (".", "..") for part in path.parts):
        raise ValueError("迁移包包含非法路径")
    if len(path.parts) < 2 or path.parts[0] not in DATA_FOLDERS:
        raise ValueError("迁移包只能包含账本、旧记录和导出文件")
    for part in path.parts:
        if re.search(r'[<>:"|?*\x00-\x1f]', part) or part.endswith((" ", ".")) or re.fullmatch(r"(?i)(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?", part):
            raise ValueError("迁移包包含无效 Windows 文件名")
    if path.parts[0] == "ledger" and name != LEDGER_PATH:
        raise ValueError("迁移包包含未知账本文件")
    return path


def read_archive(archive_path, company_name):
    try:
        with zipfile.ZipFile(archive_path) as archive:
            entries = archive.infolist()
            if len(entries) > MAX_FILES or sum(i.file_size for i in entries) > MAX_EXPANDED_BYTES:
                raise ValueError("迁移包超过文件数量或解压容量限制")
            names = [i.filename for i in entries]
            if len(names) != len(set(n.casefold() for n in names)):
                raise ValueError("迁移包中存在重复文件名")
            if "manifest.json" not in names or archive.getinfo("manifest.json").file_size > 4 * 1024 * 1024:
                raise ValueError("迁移包清单缺失或过大")
            manifest = json.loads(archive.read("manifest.json"))
            if manifest.get("schema_version") != 1 or manifest.get("kind") != "xunwen-data-transfer":
                raise ValueError("迁移包格式版本不支持")
            if name_key(manifest.get("company_name", "")) != name_key(company_name):
                raise ValueError("迁移包公司与软件配置不符")
            records = manifest["files"]
            if not isinstance(records, list) or len(records) != len(names) - 1:
                raise ValueError("迁移包文件清单不完整")
            indexed = {r["path"]: r for r in records}
            if len(indexed) != len(records) or set(indexed) != set(names) - {"manifest.json"} or LEDGER_PATH not in indexed:
                raise ValueError("迁移包文件清单不一致")
            folded = {name.casefold() for name in indexed}
            if any(parent.as_posix().casefold() in folded for name in indexed for parent in PurePosixPath(name).parents):
                raise ValueError("迁移包存在文件与父目录路径冲突")
            content = {}
            for entry in entries:
                if entry.filename == "manifest.json":
                    continue
                check_member(entry.filename)
                if entry.is_dir() or stat.S_ISLNK(entry.external_attr >> 16) or entry.flag_bits & 1:
                    raise ValueError("迁移包不支持目录条目、符号链接或加密条目")
                value = archive.read(entry)
                record = indexed[entry.filename]
                if len(value) != record["size"] or digest(value) != record["sha256"]:
                    raise ValueError("迁移包文件校验失败：" + entry.filename)
                content[entry.filename] = value
            ledger = json.loads(content[LEDGER_PATH])
            validate_ledger(ledger)
            if name_key(ledger["company"]["name"]) != name_key(company_name) or ledger["revision"] != manifest["revision"]:
                raise ValueError("迁移包账本公司或版本不符")
            return manifest, content
    except (zipfile.BadZipFile, KeyError, TypeError, AttributeError, UnicodeError) as exc:
        raise ValueError("迁移包损坏或格式不完整：" + str(exc)) from exc
