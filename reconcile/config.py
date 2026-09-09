import json
from pathlib import Path


def load_config(root):
    config = json.loads((Path(root) / "config" / "defaults.json").read_text(encoding="utf-8"))
    if "companies" not in config:
        # 从旧软件目录备份时，仅在内存补齐公司目录，不回写旧配置。
        defaults = Path(__file__).resolve().parents[1] / "config" / "defaults.json"
        config["companies"] = json.loads(defaults.read_text(encoding="utf-8"))["companies"]
    return config
