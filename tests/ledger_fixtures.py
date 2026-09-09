import base64
import io
from openpyxl import Workbook


def bank_row(reference="R1", amount=1200000, party="测试供应商", date="2026-08-03", **extra):
    return dict(id="source-bank", date=date, party=party, currency="CNY", amount_cents=amount,
                debit_cents=amount, credit_cents=0, direction="支出", summary="货款", reference=reference,
                account="443000000000000000001", bank="交通银行", type="转账", source="流水.xlsx", sheet="流水", row=4,
                source_sequence="1", file_hash="test-bank", duplicate=False, **extra)


def invoice_row(number="N1", amount=1200000, party="测试供应商", date="2026-09-03", **extra):
    return dict(id="source-invoice", date=date, party=party, currency="CNY", amount_cents=amount,
                number=number, code="", summary="货款", source_status="正常", red=False, invalid="",
                source="发票.xlsx", sheet="发票", row=3, source_sequence="1", file_hash="test-invoice", **extra)


def upload(rows, kind, company, name=None):
    book = Workbook()
    sheet = book.active
    if kind == "bank":
        sheet.append(["交通银行明细对账单"])
        sheet.append(["账号：", "443000000000000000001", "户名：", company, "币种：", "人民币"])
        sheet.append(["序号", "交易日期", "对方户名", "借方发生额", "贷方发生额", "摘要", "流水号"])
        for n, r in enumerate(rows, 1):
            sheet.append([n, r["date"], r["party"], r["debit_cents"] / 100, r["credit_cents"] / 100, r["summary"], r["reference"]])
    else:
        sheet.append([company + "进项发票清单"])
        sheet.append(["序号", "发票号码", "开票日期", "销方名称", "价税合计", "红字蓝字", "发票状态", "发票代码", "货物、应税劳务及服务"])
        for n, r in enumerate(rows, 1):
            sheet.append([n, r["number"], r["date"], r["party"], r["amount_cents"] / 100, "红字" if r["red"] else "蓝字", r["source_status"], r["code"], r["summary"]])
    data = io.BytesIO()
    book.save(data)
    return {"name": name or kind + ".xlsx", "content": base64.b64encode(data.getvalue()).decode("ascii")}
