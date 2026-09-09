import csv
import json
import uuid
from datetime import datetime
from decimal import Decimal
from pathlib import Path


def safe_cell(value):
    # 对用户文本禁用公式起始符，防止 Excel 把导入内容当作公式执行。
    if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")):
        return "'" + value
    if isinstance(value, str) and value.isdigit() and len(value) > 15:
        return "'" + value
    return value


def money(amount):
    return f"{amount / 100:.2f}"


def write_csv(path, headers, rows):
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(headers)
        writer.writerows([[safe_cell(cell) for cell in row] for row in rows])


def export_reports(result, root, statuses):
    directory = Path(root) / (datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:6])
    directory.mkdir(parents=True, exist_ok=True)
    invoice_lookup = {i["id"]: i for i in result["invoices"]}
    bank_rows = []
    for r in result["bank"]:
        related = [invoice_lookup[i] for i in r["invoice_ids"]]
        bank_rows.append([
            r["id"], r["date"], r["direction"], r["party"], r["summary"], r["currency"],
            Decimal(r["debit_cents"]).scaleb(-2), Decimal(r["credit_cents"]).scaleb(-2), statuses[r["status"]], r["reason"],
            " / ".join(i["number"] for i in related), " / ".join(i["party"] for i in related),
            " / ".join(i["date"] for i in related), " / ".join(f"{i['id']}:{money(i['amount_cents'])}" for i in related),
            r["reference"], r["source"], r["sheet"], r["row"]
        ])
    write_csv(directory / "流水对账结果.csv", ["流水编号", "交易日期", "方向", "对方户名", "摘要", "币种", "支出金额", "收入金额", "对账结果", "匹配依据或原因", "关联发票号码（多张用斜线分隔）", "关联销方", "开票日期", "关联发票金额明细（合并匹配勿重复求和）", "银行流水号", "原始文件", "工作表", "原始行号"], bank_rows)
    write_csv(directory / "发票核对明细.csv", ["发票编号", "发票号码（文本）", "开票日期", "销方名称", "币种", "价税合计", "红蓝字", "原票状态", "对账状态", "关联流水编号", "项目", "原始文件", "工作表", "原始行号"], [
        [i["id"], "'" + i["number"], i["date"], i["party"], i["currency"], Decimal(i["amount_cents"]).scaleb(-2), "红字" if i["red"] else "蓝字", i["source_status"], i["reconciliation_status"], " / ".join(i["bank_ids"]), i["summary"], i["source"], i["sheet"], i["row"]]
        for i in result["invoices"]
    ])
    stats = result["stats"]
    lines = ["# 对账结果", "", f"流水文件：{result['bank_name']}", f"发票文件：{result['invoice_name']}", "", "| 分类 | 笔数 | 支出金额 | 收入金额 |", "| --- | ---: | ---: | ---: |"]
    for key, label in statuses.items():
        item = stats[key]
        lines.append(f"| {label} | {item['count']} | {money(item['debit_cents'])} | {money(item['credit_cents'])} |")
    lines.extend(["", f"总计 {stats['bank_count']} 笔流水，支出 {money(stats['debit_cents'])} 元，收入 {money(stats['credit_cents'])} 元。", f"发票 {stats['invoice_count']} 张，红字 {stats['red_count']} 张，价税合计净额 {money(stats['invoice_net_cents'])} 元。", "", "## 口径", "", "仅判断已导入文件之间是否匹配。未匹配不等于确定未开票，可能跨期开票、平台代收、合并付款或使用其他账户。已匹配是依据名称和金额的文件比对结论，并非税务平台验真。", "", "以发票价税合计匹配支出；收入不与进项发票比对。红字、异常票与重复号码不自动使用。多笔与多票合并需要人工确认。", "", "CSV 使用 UTF-8 BOM。发票号码前置单引号保护长号码；Excel 中建议通过“数据 → 从文本/CSV”导入，并将号码与流水号列设为文本。", "", "人工确认依据：", ""])
    lines.extend([f"- {d['at']}：{','.join(d['bank_ids'])} / {','.join(d['invoice_ids']) or '不参与比对'}；{d['note']}" for d in result["decisions"]] or ["无人工确认。"])
    lines.extend(["", "导入检查：", ""] + ["- " + note for note in result.get("notes", [])])
    (directory / "对账说明.md").write_text("\n".join(lines), encoding="utf-8")
    (directory / "完整对账记录.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return directory
