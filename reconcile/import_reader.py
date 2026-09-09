import base64
import hashlib
from pathlib import Path
from .excel_reader import read_sheets
from .parsers import parse_bank, parse_invoices
from .company_identity import bank_identity, verify_invoice_company


def read_uploads(payload, config):
    parsed = {"bank": [], "invoices": [], "files": [], "notes": [], "controls": []}
    for key in ("bank", "invoice"):
        item = payload.get(key)
        if item is None:
            continue
        if not isinstance(item, dict) or not isinstance(item.get("name"), str) or not isinstance(item.get("content"), str):
            raise ValueError("上传文件必须包含文件名 name 与 Base64 文本 content")
        name = Path(item["name"]).name
        try:
            content = base64.b64decode(item.get("content", ""), validate=True)
        except (ValueError, TypeError):
            raise ValueError("上传内容无效，请重新选择文件") from None
        if not content or len(content) > config["max_upload_mb"] * 1024 * 1024:
            raise ValueError(f"每个文件必须大于 0 且不超过 {config['max_upload_mb']} MB")
        sheets = read_sheets(content, name, config["max_rows"])
        if key == "bank":
            identities = bank_identity(sheets, config)
            rows, notes, controls = parse_bank(sheets, name, config)
            for row in rows:
                row.update(identities[row["sheet"]], duplicate=False)
            if any(not c["passed"] for c in controls):
                raise ValueError("银行借贷合计与页脚控制数不符，请核对完整文件；整次导入取消")
            parsed["bank"] = rows
            parsed["controls"] = controls
        else:
            verify_invoice_company(sheets, config)
            rows, notes = parse_invoices(sheets, name, config, mark_duplicates=False)
            parsed["invoices"] = rows
        if any(r["currency"] != "CNY" for r in rows):
            raise ValueError("当前账本仅支持人民币，整次导入取消")
        file_info = {"kind": key, "name": name, "sha256": hashlib.sha256(content).hexdigest(), "size": len(content), "rows": len(rows)}
        for row in rows:
            row["file_hash"] = file_info["sha256"]
        parsed["files"].append(file_info)
        parsed["notes"].extend(notes)
    if not parsed["files"]:
        raise ValueError("请至少选择一份流水或进项发票清单")
    return parsed
