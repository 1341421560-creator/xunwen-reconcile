from datetime import datetime, timezone
from uuid import uuid4


def timestamp():
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def identifier(prefix):
    return prefix + uuid4().hex


def record_event(ledger, action, note, **details):
    event = dict(id=identifier("E"), at=timestamp(), action=action,
                 note=note, revision=ledger["revision"] + 1, **details)
    ledger["audit"].append(event)
    return event
