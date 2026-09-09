from .identity_dedup import identity
from .invoice_difference import validate_difference
from .summary_exclusions import normalize_keywords
from .bank_notes import validate_bank_note
from .expense_categories import validate_expense_override


class LedgerCorruptionError(ValueError):
    pass


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def validate_ledger(ledger):
    _require(isinstance(ledger, dict), "账本根结构必须为对象")
    _require(ledger.get("schema_version") == 1 and type(ledger.get("revision")) is int and ledger["revision"] >= 0, "账本版本无效")
    for field in ("bank", "invoices", "allocations", "conflicts", "batches", "audit", "blocked_pairs", "migrations"):
        _require(isinstance(ledger.get(field), list), f"账本 {field} 结构无效")
    company = ledger.get("company")
    _require(isinstance(company, dict) and isinstance(company.get("id"), str) and bool(company["id"]) and isinstance(company.get("name"), str) and bool(company["name"]), "账本公司结构无效")
    settings = ledger.get("settings")
    _require(isinstance(settings, dict) and isinstance(settings.get("aliases"), dict) and type(settings.get("exclude_special")) is bool, "账本规则结构无效")
    _require(all(isinstance(k, str) and isinstance(v, str) and k.strip() and v.strip() for k, v in settings["aliases"].items()), "账本名称映射无效")
    normalize_keywords(settings.get("custom_exclude_keywords", []))
    maps = {}
    for kind in ("bank", "invoices", "allocations", "conflicts", "batches", "audit"):
        mapping = {}
        for row in ledger[kind]:
            _require(isinstance(row, dict) and isinstance(row.get("id"), str) and row["id"] and row["id"] not in mapping, f"账本 {kind} 有无效或重复编号")
            mapping[row["id"]] = row
        maps[kind] = mapping
    for kind in ("bank", "invoices"):
        identities = set()
        for row in ledger[kind]:
            _require(type(row.get("amount_cents")) is int and row.get("company_id") == company["id"] and row.get("currency") == "CNY", "账本金额、币种或公司归属无效")
            _require(all(isinstance(row.get(f), str) for f in ("date", "party", "summary")) and isinstance(row.get("sources"), list), "账本来源或日期字段无效")
            key = identity(row, kind)
            if key:
                _require(key not in identities, "账本存在重复金融记录标识")
                identities.add(key)
            if kind == "bank":
                validate_bank_note(row)
                validate_expense_override(row)
                debit, credit = row.get("debit_cents"), row.get("credit_cents")
                _require(type(debit) is int and type(credit) is int and debit >= 0 and credit >= 0 and debit+credit == row["amount_cents"] and row["amount_cents"] > 0, "付款原金额与借贷金额不一致")
                _require((row.get("direction") == "支出" and debit > 0 and credit == 0) or (row.get("direction") == "收入" and credit > 0 and debit == 0), "付款方向无效")
            else:
                _require(type(row.get("red")) is bool and isinstance(row.get("invalid"), str), "发票异常状态无效")
                _require(row["amount_cents"] >= 0 or row["red"], "负金额发票未标为红字")
                if "difference" in row:
                    validate_difference(row["difference"])
    bt, it = {}, {}
    for a in ledger["allocations"]:
        _require(a.get("bank_id") in maps["bank"] and a.get("invoice_id") in maps["invoices"], "关联记录引用不存在")
        _require(a.get("state") in ("active", "revoked") and a.get("kind") in ("auto", "manual", "migrated"), "关联状态或方式无效")
        _require(type(a.get("amount_cents")) is int and a["amount_cents"] > 0, "核销金额必须为正整数分")
        if a["state"] != "active":
            continue
        b, i = maps["bank"][a["bank_id"]], maps["invoices"][a["invoice_id"]]
        _require(b["direction"] == "支出" and b["currency"] == i["currency"] and not i["red"] and not i["invalid"], "有效关联方向或发票状态无效")
        bt[b["id"]] = bt.get(b["id"], 0) + a["amount_cents"]
        it[i["id"]] = it.get(i["id"], 0) + a["amount_cents"]
    _require(all(v <= maps["bank"][k]["amount_cents"] for k, v in bt.items()), "付款累计分配超额")
    _require(all(v <= maps["invoices"][k]["amount_cents"] for k, v in it.items()), "发票累计分配超额")
    for c in ledger["conflicts"]:
        _require(c.get("kind") in ("bank", "invoices") and c.get("state") in ("pending", "resolved") and c.get("type") in ("version", "missing_reference"), "冲突状态无效")
        _require(isinstance(c.get("record_ids"), list) and c["record_ids"] and all(rid in maps[c["kind"]] for rid in c["record_ids"]), "冲突引用记录不存在")
    for pair in ledger["blocked_pairs"]:
        _require(isinstance(pair, list) and len(pair) == 2 and pair[0] in maps["bank"] and pair[1] in maps["invoices"], "撤回组合引用无效")
