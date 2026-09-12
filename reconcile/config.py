import json
from pathlib import Path


def load_config(root):
    config = json.loads((Path(root) / "config" / "defaults.json").read_text(encoding="utf-8"))
    defaults_path = Path(__file__).resolve().parents[1] / "config" / "defaults.json"
    defaults = json.loads(defaults_path.read_text(encoding="utf-8"))
    # 读取旧软件目录或备份时只补齐新增显示配置，不修改原配置文件。
    config.setdefault("offset_statuses", defaults["offset_statuses"])
    config["difference_statuses"] = {**defaults["difference_statuses"], **config.get("difference_statuses", {})}
    if "companies" not in config:
        # 从旧软件目录备份时，仅在内存补齐公司目录，不回写旧配置。
        defaults = Path(__file__).resolve().parents[1] / "config" / "defaults.json"
        config["companies"] = json.loads(defaults.read_text(encoding="utf-8"))["companies"]
    formats = Path(root) / "config" / "bank_formats.json"
    if not formats.exists():
        formats = Path(__file__).resolve().parents[1] / "config" / "bank_formats.json"
    config["bank_formats"] = json.loads(formats.read_text(encoding="utf-8"))
    pdf = Path(root) / "config" / "pdf_import.json"
    if not pdf.exists():
        pdf = Path(__file__).resolve().parents[1] / "config" / "pdf_import.json"
    config["pdf_import"] = json.loads(pdf.read_text(encoding="utf-8"))
    return config
