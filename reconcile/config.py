import json
from pathlib import Path


def load_config(root):
    return json.loads((Path(root) / "config" / "defaults.json").read_text(encoding="utf-8"))
