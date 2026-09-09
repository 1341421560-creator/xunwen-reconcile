import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path


class SessionStore:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)

    def new_id(self):
        return uuid.uuid4().hex

    def save(self, session):
        session["saved_at"] = datetime.now(timezone.utc).isoformat()
        path = self.directory / (session["id"] + ".json")
        path.write_text(json.dumps(session, ensure_ascii=False, indent=2), encoding="utf-8")

    def load(self, session_id):
        if not re.fullmatch(r"[a-f0-9]{32}", session_id):
            raise ValueError("无效的对账记录编号")
        path = self.directory / (session_id + ".json")
        if not path.is_file():
            raise ValueError("对账记录不存在")
        return json.loads(path.read_text(encoding="utf-8"))

    def list(self):
        result = []
        for path in self.directory.glob("*.json"):
            try:
                session = json.loads(path.read_text(encoding="utf-8"))
                result.append({"id": session["id"], "saved_at": session["saved_at"], "bank_name": session["bank_name"], "invoice_name": session["invoice_name"]})
            except (ValueError, KeyError):
                continue
        return sorted(result, key=lambda item: item["saved_at"], reverse=True)
