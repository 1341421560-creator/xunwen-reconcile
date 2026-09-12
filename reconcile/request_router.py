from pathlib import Path
from .company_profiles import DEFAULT_COMPANY, company_profile, safe_company_path
from .company_registry import CompanyRegistry


ACTIONS = {
    "/api/import": "import_files", "/api/review": "review", "/api/undo": "undo",
    "/api/settings": "settings", "/api/export": "export", "/api/restore": "restore",
    "/api/ledger": "ledger", "/api/conflict": "resolve", "/api/exception": "exception",
    "/api/exclusion": "exclusion", "/api/invoice-difference": "invoice_difference",
    "/api/bank-note": "bank_note", "/api/expense-category": "expense_category",
    "/api/expense-statistics": "expense_statistics", "/api/conflict-undo": "reverse_conflict",
    "/api/exception-undo": "reverse_exception",
    "/api/invoice-selection": "invoice_selection",
    "/api/invoice-offset/preview": "invoice_offset_preview",
    "/api/invoice-offset": "invoice_offset",
    "/api/invoice-offset/undo": "invoice_offset_undo",
}
READ_ACTIONS = {"/api/ledger", "/api/restore", "/api/expense-statistics", "/api/invoice-offset/preview"}


class RequestRouter:
    def __init__(self, target):
        self.registry = target if isinstance(target, CompanyRegistry) else CompanyRegistry(target.root, target.config, target)
        self.root, self.config = self.registry.root, self.registry.config

    def key(self, payload, required=False):
        if required and "company_key" not in payload:
            raise ValueError("缺少公司标识，请刷新软件页面并选择公司后重新提交")
        key = payload.get("company_key", DEFAULT_COMPANY)
        company_profile(self.config, key)
        return key

    def bootstrap(self, payload):
        key = self.key(payload)
        profile = self.registry.describe(key)
        if profile["initialized"]:
            service = self.registry.get(key)
            with service.lock:
                result = service.bootstrap()
            result["result"]["company_key"] = key
        else:
            result = {"app_name": self.config["app_name"], "statuses": self.config["statuses"], "history": [], "result": None}
        return {**result, "company_key": key, "company_profile": profile,
                "companies": self.registry.companies(), "setup_required": not profile["initialized"]}

    def get(self, route, payload):
        if route == "/api/companies":
            return {"default_company_key": DEFAULT_COMPANY, "companies": self.registry.companies()}
        if route == "/api/bootstrap":
            return self.bootstrap(payload)
        if route == "/api/ledger":
            return self.post(route, payload)
        return None

    def post(self, route, payload):
        if route == "/api/companies/activate":
            self.registry.activate(payload)
            return self.bootstrap(payload)
        method = ACTIONS.get(route)
        if method is None:
            return None
        key = self.key(payload, route not in READ_ACTIONS or route == "/api/invoice-offset/preview")
        service = self.registry.get(key)
        with service.lock:
            result = getattr(service, method)(payload)
        result["company_key"] = key
        if route == "/api/export":
            for item in result["files"]:
                item["url"] = f"/reports/{key}/{Path(result['directory']).name}/{item['name']}"
        return result

    def file(self, route):
        if route.startswith("/reports/"):
            relative = route[len("/reports/"):]
            first, separator, tail = relative.partition("/")
            keys = {p["key"] for p in self.config["companies"]}
            key = first if first in keys else DEFAULT_COMPANY
            if first in keys:
                relative = tail if separator else ""
            service = self.registry.get(key)
            base = safe_company_path(self.root, self.root / service.config["report_dir"])
        else:
            base = self.root / "web"
            relative = "index.html" if route == "/" else route.lstrip("/")
        path = (base / relative).resolve()
        if not path.is_relative_to(base.resolve()) or not path.is_file():
            return None
        return path
