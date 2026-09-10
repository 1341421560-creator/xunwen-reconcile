import base64
import hashlib
from pathlib import Path
from .excel_reader import read_sheets
from .parsers import parse_invoices
from .company_identity import verify_invoice_company
from .bank_import import read_bank
from .bocom_pdf_import import read_bank_pdf


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
        if key == "bank":
            if Path(name).suffix.lower() == ".pdf":
                rows, notes, controls = read_bank_pdf(content, name, config)
            else:
                rows, notes, controls = read_bank(read_sheets(content, name, config["max_rows"]), name, config)
            parsed["bank"] = rows
            parsed["controls"] = controls
        else:
            if Path(name).suffix.lower() == ".pdf":
                raise ValueError("PDF 仅用于银行流水导入，进项发票仍请选择原有 Excel 清单")
            sheets = read_sheets(content, name, config["max_rows"])
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
