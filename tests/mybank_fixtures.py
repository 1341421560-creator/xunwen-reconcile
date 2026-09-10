import base64
import io
import zipfile
from decimal import Decimal
from xml.etree.ElementTree import Element, SubElement, tostring


def mybank_row(reference="MY0000000000000000000000000001", income=None, payment="12000.00", party="测试供应商", date="2026-03-30 16:52:43", note="货款"):
    return [reference, None, date, "跨行转账", income, payment, "25000.00", party, "00000000000000000001", "测试对方银行", note]


def mybank_sheet(company, rows=None, account="8888000000000001", name="网商流水"):
    rows = rows if rows is not None else [mybank_row(), mybank_row("MY-INCOME", income="25000.00", payment=None, party="测试客户")]
    incoming = sum((Decimal(str(row[4])) for row in rows if row[4] is not None), Decimal(0))
    outgoing = sum((Decimal(str(row[5])) for row in rows if row[5] is not None), Decimal(0))
    return {"name": name, "rows": [["浙江网商银行企业账户交易明细"],
            ["企业名称", company, None, None, "企业账号", account + "(人民币)"],
            ["借方交易笔数", str(sum(bool(row[4]) for row in rows)) + "笔", None, None, "借方交易金额", "￥" + str(incoming)],
            ["贷方交易笔数", str(sum(bool(row[5]) for row in rows)) + "笔", None, None, "贷方交易金额", "￥" + str(outgoing)],
            ["账务流水号", "提交时间", "交易时间", "交易名称", "借方金额(收)", "贷方金额(支)", "余额", "对方户名", "对方账号", "对方机构", "备注"], *rows]}


def workbook_upload(sheets, filename="银行明细.xlsx"):
    # 直接构建内存中的最小 OOXML 夹具，测试不依赖用户原件或额外制表环境。
    ns = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    relationships = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    content_types = Element("Types", xmlns="http://schemas.openxmlformats.org/package/2006/content-types")
    SubElement(content_types, "Default", Extension="rels", ContentType="application/vnd.openxmlformats-package.relationships+xml")
    SubElement(content_types, "Default", Extension="xml", ContentType="application/xml")
    SubElement(content_types, "Override", PartName="/xl/workbook.xml", ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml")
    workbook = Element("workbook", xmlns=ns)
    sheet_list = SubElement(workbook, "sheets")
    rels = Element("Relationships", xmlns="http://schemas.openxmlformats.org/package/2006/relationships")
    root_rels = Element("Relationships", xmlns="http://schemas.openxmlformats.org/package/2006/relationships")
    SubElement(root_rels, "Relationship", Id="rId1", Type=relationships + "/officeDocument", Target="xl/workbook.xml")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for number, sheet in enumerate(sheets, 1):
            SubElement(sheet_list, "sheet", name=sheet["name"], sheetId=str(number), attrib={"{" + relationships + "}id": f"rId{number}"})
            SubElement(rels, "Relationship", Id=f"rId{number}", Type=relationships + "/worksheet", Target=f"worksheets/sheet{number}.xml")
            SubElement(content_types, "Override", PartName=f"/xl/worksheets/sheet{number}.xml", ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml")
            worksheet = Element("worksheet", xmlns=ns)
            data = SubElement(worksheet, "sheetData")
            for row_number, row in enumerate(sheet["rows"], 1):
                element = SubElement(data, "row", r=str(row_number))
                for column, value in enumerate(row, 1):
                    if value is None:
                        continue
                    numeric = isinstance(value, (int, float)) and not isinstance(value, bool)
                    cell = SubElement(element, "c", r=f"{chr(64 + column)}{row_number}", t="n" if numeric else "inlineStr")
                    if numeric:
                        SubElement(cell, "v").text = str(value)
                    else:
                        SubElement(SubElement(cell, "is"), "t").text = str(value)
            archive.writestr(f"xl/worksheets/sheet{number}.xml", tostring(worksheet, encoding="utf-8"))
        for name, element in (("[Content_Types].xml", content_types), ("_rels/.rels", root_rels), ("xl/workbook.xml", workbook), ("xl/_rels/workbook.xml.rels", rels)):
            archive.writestr(name, tostring(element, encoding="utf-8"))
    return {"name": filename, "content": base64.b64encode(buffer.getvalue()).decode("ascii")}
