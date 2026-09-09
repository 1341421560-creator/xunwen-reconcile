import io
import json
import stat
import zipfile
from datetime import datetime
from .archive_validation import LEDGER_PATH, MAX_EXPANDED_BYTES, MAX_FILES, digest, read_archive
from reconcile.company_profiles import DEFAULT_COMPANY, company_profile


def encode_single(key, ledger, content):
    manifest = {"schema_version": 1, "kind": "xunwen-data-transfer", "company_key": key,
                "created_at": datetime.now().astimezone().isoformat(),
                "company_name": ledger["company"]["name"], "revision": ledger["revision"],
                "files": [{"path": name, "size": len(value), "sha256": digest(value)} for name, value in sorted(content.items())]}
    return encode_zip(manifest, content)


def encode_zip(manifest, content):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
        for name, value in content.items():
            archive.writestr(name, value)
    return output.getvalue()


def inspect_manifest(archive):
    entries = archive.infolist()
    if len(entries) > MAX_FILES or sum(entry.file_size for entry in entries) > MAX_EXPANDED_BYTES:
        raise ValueError("迁移包超过文件数量或解压容量限制")
    names = [entry.filename for entry in entries]
    if len(names) != len(set(name.casefold() for name in names)):
        raise ValueError("迁移包中存在重复文件名")
    if "manifest.json" not in names or archive.getinfo("manifest.json").file_size > 4 * 1024 * 1024:
        raise ValueError("迁移包清单缺失或过大")
    if any(entry.is_dir() or stat.S_ISLNK(entry.external_attr >> 16) or entry.flag_bits & 1 for entry in entries):
        raise ValueError("迁移包不支持目录、符号链接或加密条目")
    return json.loads(archive.read("manifest.json"))


def validate_single(source, config, key=None):
    with zipfile.ZipFile(source) as archive:
        manifest = inspect_manifest(archive)
    bound = manifest.get("company_key", DEFAULT_COMPANY)
    company_profile(config, bound)
    if key is not None and bound != key:
        raise ValueError("单公司迁移包公司标识与选择不符")
    name = manifest.get("company_name")
    if not isinstance(name, str) or not name.strip() or len(name) > 200 or any(ord(c) < 32 for c in name):
        raise ValueError("迁移包公司完整名称无效")
    if hasattr(source, "seek"):
        source.seek(0)
    checked, content = read_archive(source, name)
    ledger = json.loads(content[LEDGER_PATH])
    if ledger["company"].get("key", bound) != bound:
        raise ValueError("迁移包账本绑定公司与清单不符")
    return {"key": bound, "legal_name": name, "initialized": True, "has_ledger": True,
            "revision": checked["revision"], "content": content}


def read_companies(source, config):
    try:
        with zipfile.ZipFile(source) as archive:
            manifest = inspect_manifest(archive)
            if manifest.get("schema_version") == 1:
                return [validate_single(source, config)]
            if manifest.get("schema_version") != 2 or manifest.get("kind") != "xunwen-companies-transfer":
                raise ValueError("迁移包格式版本不支持")
            records = manifest["companies"]
            if not isinstance(records, list) or len(records) != len(config["companies"]):
                raise ValueError("多公司迁移包公司清单不完整")
            result, seen, expected, total_size, total_files = [], set(), {"manifest.json"}, 0, 0
            for record in records:
                key = record["key"]
                company_profile(config, key)
                if key in seen:
                    raise ValueError("多公司迁移包包含重复公司")
                seen.add(key)
                if type(record["initialized"]) is not bool or type(record["has_ledger"]) is not bool:
                    raise ValueError("多公司迁移包启用状态无效")
                if not record["has_ledger"]:
                    if record.get("archive") or record.get("revision") is not None:
                        raise ValueError("无账本公司不能包含数据文件或版本")
                    if record["initialized"] and key != DEFAULT_COMPANY or not record["initialized"] and record.get("legal_name"):
                        raise ValueError("未启用公司状态与名称不一致")
                    if record["initialized"] and (not isinstance(record.get("legal_name"), str) or not record["legal_name"].strip()):
                        raise ValueError("已启用公司的完整名称缺失")
                    result.append({**record, "content": {}})
                    continue
                filename = f"companies/{key}.zip"
                if record.get("archive") != filename or not record["initialized"]:
                    raise ValueError("公司归档路径或启用状态不一致")
                value = archive.read(filename)
                if len(value) != record["size"] or digest(value) != record["sha256"]:
                    raise ValueError("公司归档文件校验失败：" + key)
                checked = validate_single(io.BytesIO(value), config, key)
                if checked["legal_name"] != record["legal_name"] or checked["revision"] != record["revision"]:
                    raise ValueError("公司清单名称或版本与账本不一致")
                total_size += sum(len(data) for data in checked["content"].values())
                total_files += len(checked["content"])
                if total_size > MAX_EXPANDED_BYTES or total_files > MAX_FILES:
                    raise ValueError("全部公司数据超过解压容量或文件数量限制")
                result.append(checked)
                expected.add(filename)
            if set(archive.namelist()) != expected:
                raise ValueError("多公司迁移包文件清单不一致")
            return result
    except (zipfile.BadZipFile, KeyError, TypeError, AttributeError, UnicodeError) as exc:
        raise ValueError("迁移包损坏或格式不完整：" + str(exc)) from exc
