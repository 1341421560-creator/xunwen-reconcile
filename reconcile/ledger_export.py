import json
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from .audit import identifier, timestamp
from .export import write_csv
from .ledger_progress import statistics


def amount(n):
    return Decimal(n).scaleb(-2)


def export_ledger(view, root, labels, month=""):
    if month and month not in view["months"]:
        raise ValueError("所选付款月份不存在")
    directory = Path(root) / (datetime.now().strftime("%Y%m%d_%H%M%S") + "_累计_" + identifier("")[:8])
    directory.mkdir(parents=True, exist_ok=False)
    at = timestamp()
    banks = [b for b in view["bank"] if not month or b["date"].startswith(month)]
    bids = {b["id"] for b in banks}
    allocations = [a for a in view["allocations"] if a["bank_id"] in bids]
    iids = {a["invoice_id"] for a in allocations}
    invoices = [i for i in view["invoices"] if not month or i["id"] in iids]
    bm, im = {b["id"]: b for b in banks}, {i["id"]: i for i in invoices}
    write_csv(directory / "流水对账结果.csv", ["流水编号", "付款日期", "对方户名", "摘要", "方向", "币种", "支出金额", "收入金额", "累计有效核销金额", "剩余金额", "状态", "依据", "银行账号", "银行流水号", "原文件", "原工作表", "原行号", "关联发票差额提示", "导出时间"], [
        [b["id"], b["date"], b["party"], b["summary"], b["direction"], b["currency"], amount(b["debit_cents"]), amount(b["credit_cents"]), amount(b["allocated_cents"]), amount(b["remaining_cents"]), labels[b["status"]], b["reason"], b.get("account", ""), b.get("reference", ""), b.get("source", ""), b.get("sheet", ""), b.get("row", ""), b["difference_hint"], at] for b in banks])
    write_csv(directory / "发票余额明细.csv", ["发票编号", "发票代码", "发票号码", "实际开票日期", "销方", "票面价税合计", "全账本已分配金额", "全账本未分配余额", "状态", "源状态", "可继续分配金额", "差额状态", "差额处理依据", "差额更新时间", "导出时间"], [
        [i["id"], i.get("code", ""), "'" + i["number"], i["date"], i["party"], amount(i["amount_cents"]), amount(i["allocated_cents"]), amount(i["remaining_cents"]), labels[i["status"]], i.get("source_status", ""), amount(i["distributable_cents"]), view["difference_labels"][i["difference_status"]], i["difference_note"], i["difference_updated_at"] or "", at] for i in invoices])
    write_csv(directory / "逐条金额分配明细.csv", ["关联编号", "关联状态", "付款编号", "付款日期", "收款方", "发票编号", "发票号码", "实际开票日期", "票面金额（不可按本表求和）", "本次核销金额", "有效核销金额（可求和）", "建立时间", "方式", "核对依据", "撤回时间", "撤回依据", "导出时间"], [
        [a["id"], "有效" if a["state"] == "active" else "已撤回", a["bank_id"], bm[a["bank_id"]]["date"], bm[a["bank_id"]]["party"], a["invoice_id"], "'" + im[a["invoice_id"]]["number"], im[a["invoice_id"]]["date"], amount(im[a["invoice_id"]]["amount_cents"]), amount(a["amount_cents"]), amount(a["amount_cents"] if a["state"] == "active" else 0), a["at"], a["kind"], a["note"], a.get("revoked_at", ""), a.get("revoke_note", ""), at] for a in allocations])
    batch_ids = {s["batch_id"] for r in banks + invoices for s in r["sources"]}
    allocation_ids = {a["id"] for a in allocations}
    conflicts = [c for c in view["conflicts"] if not month or set(c["record_ids"]) & (bids if c["kind"] == "bank" else iids)]
    conflict_ids = {c["id"] for c in conflicts}
    audit = [a for a in view["audit"] if not month or a.get("bank_id") in bids or a.get("invoice_id") in iids
             or set(a.get("allocation_ids", [])) & allocation_ids or a.get("batch_id") in batch_ids
             or a.get("conflict_id") in conflict_ids or a["action"] in ("settings", "migration")]
    stats = statistics(banks, invoices, {"statuses": labels})
    snapshot = dict(view, bank=banks, invoices=invoices, allocations=allocations, stats=stats, filtered_stats=stats,
                    filtered_bank_ids=[b["id"] for b in banks], month=month, unfinished=False,
                    conflicts=conflicts, audit=audit, batches=[b for b in view["batches"] if not month or b["id"] in batch_ids],
                    export_scope=month or "全账本", exported_at=at)
    (directory / "完整对账快照.json").write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
    (directory / "导出说明.md").write_text(f"# 累计对账导出\n\n公司：{view['company']['name']}\n\n范围：{month or '全账本'}；账本版本：{view['revision']}；导出时间：{at}\n\n付款月份仅限制展示，金额进度包含截至导出时已导入的全部月份。此文件保留导出时结果，后续导入不会修改本目录。\n\n逐条分配表的“有效核销金额”可以求和；票面金额可能在多条关联中重复，不能直接求和。发票余额表每张票仅一行，余额为全账本未分配余额；差额待确认或开票金额有误时，可继续分配金额为零。流水上的关联发票差额提示可能引用同一张票，不可重复求和。当前月份导出包含该月付款曾关联的发票；全账本导出包含未关联票。\n\n红字票、冲突和撤回证据保存在完整快照中。未匹配表示累计账本中暂未找到对应发票。\n", encoding="utf-8")
    return directory
