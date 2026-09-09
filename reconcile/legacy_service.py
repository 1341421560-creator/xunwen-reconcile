import base64
import hashlib
from pathlib import Path
from threading import RLock
from .excel_reader import read_sheets
from .parsers import parse_bank, parse_invoices
from .matching import build_result
from .review import validate_decision
from .storage import SessionStore
from .export import export_reports


class ReconciliationService:
    def __init__(self, root, config):
        self.root = Path(root)
        self.config = config
        self.store = SessionStore(self.root / config["session_dir"])
        self.lock = RLock()

    def render(self, session):
        result = build_result(session["bank"], session["invoices"], session["settings"], self.config, session["decisions"])
        return dict(result, session_id=session["id"], bank_name=session["bank_name"], invoice_name=session["invoice_name"], notes=session["notes"], controls=session["controls"], settings=session["settings"], saved_at=session["saved_at"], audit=session.get("audit", []))

    def import_files(self, payload):
        uploads = []
        for key in ("bank", "invoice"):
            item = payload.get(key, {})
            name = Path(str(item.get("name", ""))).name
            try:
                content = base64.b64decode(item.get("content", ""), validate=True)
            except (ValueError, TypeError):
                raise ValueError("上传内容无效，请重新选择文件") from None
            if not content or len(content) > self.config["max_upload_mb"] * 1024 * 1024:
                raise ValueError(f"每个文件必须大于 0 且不超过 {self.config['max_upload_mb']} MB")
            uploads.append((name, content, read_sheets(content, name, self.config["max_rows"])))
        bank, bank_notes, controls = parse_bank(uploads[0][2], uploads[0][0], self.config)
        invoices, invoice_notes = parse_invoices(uploads[1][2], uploads[1][0], self.config)
        if {r["currency"] for r in bank + invoices} != {"CNY"}:
            raise ValueError("当前版本仅支持人民币对账，请先按币种拆分；不会合并不同币种的金额")
        periods_bank, periods_invoice = {r["date"][:7] for r in bank}, {i["date"][:7] for i in invoices}
        notes = bank_notes + invoice_notes
        if periods_bank != periods_invoice:
            notes.append("流水与发票月份范围不同。系统保留跨期候选，请结合实际交易核对")
        notes.append("发票金额使用价税合计；未匹配仅表示本次文件中未找到，不代表确定未开票")
        session = {"id": self.store.new_id(), "bank_name": uploads[0][0], "invoice_name": uploads[1][0], "bank_hash": hashlib.sha256(uploads[0][1]).hexdigest(), "invoice_hash": hashlib.sha256(uploads[1][1]).hexdigest(), "bank": bank, "invoices": invoices, "notes": notes, "controls": controls, "settings": {"exclude_special": True, "aliases": {}}, "decisions": [], "audit": []}
        self.store.save(session)
        return self.render(session)

    def bootstrap(self):
        history = self.store.list()
        return {"app_name": self.config["app_name"], "company_name": self.config["company_name"], "statuses": self.config["statuses"], "history": history, "result": self.render(self.store.load(history[0]["id"])) if history else None}

    def review(self, payload):
        session = self.store.load(payload["session_id"])
        decision = validate_decision(self.render(session), payload)
        session["decisions"].append(decision)
        session["audit"].append(dict(decision, action="confirm"))
        self.store.save(session)
        return self.render(session)

    def undo(self, payload):
        session = self.store.load(payload["session_id"])
        bid = payload.get("bank_id")
        decision = next((d for d in session["decisions"] if bid in d["bank_ids"]), None)
        if not decision:
            raise ValueError("该流水没有可撤回的人工确认")
        session["decisions"] = [d for d in session["decisions"] if d is not decision]
        session["audit"].append(dict(decision, action="undo"))
        self.store.save(session)
        return self.render(session)

    def settings(self, payload):
        session = self.store.load(payload["session_id"])
        if session["decisions"]:
            raise ValueError("请先撤回人工确认后再更改规则，或重新导入建立独立对账记录")
        aliases = payload.get("aliases", {})
        if not isinstance(aliases, dict) or any(not isinstance(k, str) or not isinstance(v, str) or not k.strip() or not v.strip() for k, v in aliases.items()):
            raise ValueError("名称映射必须填写完整的银行对方名称与发票销售方名称")
        session["settings"] = {"exclude_special": bool(payload.get("exclude_special", True)), "aliases": aliases}
        self.store.save(session)
        return self.render(session)

    def export(self, payload):
        session = self.store.load(payload["session_id"])
        directory = export_reports(self.render(session), self.root / self.config["report_dir"], self.config["statuses"])
        return {"directory": str(directory), "files": [{"name": path.name, "url": "/reports/" + directory.name + "/" + path.name} for path in directory.iterdir()]}
