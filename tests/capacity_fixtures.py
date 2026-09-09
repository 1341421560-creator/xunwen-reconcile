import copy
import zipfile
from decimal import Decimal
from xml.etree import ElementTree as ET


def amount_for(sequence):
    return ((sequence % 997) + 1) * 100 + sequence % 100


def bank_rows(source, count, page_size=None):
    prefix, footer = copy.deepcopy(source[:8]), copy.deepcopy(source[26:])
    debit_count = count - count // 5
    debit_total = sum(amount_for(i) for i in range(1, count + 1) if i % 5)
    credit_total = sum(amount_for(i) for i in range(1, count + 1) if not i % 5)
    for row in footer:
        first = str(row[0])
        if "发生数" in first:
            row[2], row[10] = str(debit_count), str(count // 5)
        if "发生额" in first:
            row[2], row[10] = format(Decimal(debit_total).scaleb(-2), ",.2f"), format(Decimal(credit_total).scaleb(-2), ",.2f")
    rows = prefix[:]
    for sequence in range(1, count + 1):
        if page_size and sequence > 1 and (sequence - 1) % page_size == 0:
            rows.extend(copy.deepcopy(footer))
            rows.extend(copy.deepcopy(prefix))
        row = copy.deepcopy(source[11])
        row[0] = str(sequence)
        row[6], row[7] = "", ""
        row[6 if sequence % 5 else 7] = format(Decimal(amount_for(sequence)).scaleb(-2), ",.2f")
        row[12] = f"容量验证供应商{sequence:06d}"
        row[14] = "容量验证货款"
        row[15] = f"CAPACITY{sequence:08d}"
        rows.append(row)
    rows.extend(footer)
    return rows, {"count": count, "debit_cents": debit_total, "credit_cents": credit_total, "last_sequence": str(count), "physical_rows": len(rows)}


def write_fods(path, sheets):
    office = "urn:oasis:names:tc:opendocument:xmlns:office:1.0"
    table = "urn:oasis:names:tc:opendocument:xmlns:table:1.0"
    text = "urn:oasis:names:tc:opendocument:xmlns:text:1.0"
    for prefix, namespace in (("office", office), ("table", table), ("text", text)):
        ET.register_namespace(prefix, namespace)
    root = ET.Element(f"{{{office}}}document", {f"{{{office}}}version": "1.2", f"{{{office}}}mimetype": "application/vnd.oasis.opendocument.spreadsheet"})
    body = ET.SubElement(root, f"{{{office}}}body")
    spreadsheet = ET.SubElement(body, f"{{{office}}}spreadsheet")
    for name, rows in sheets:
        sheet = ET.SubElement(spreadsheet, f"{{{table}}}table", {f"{{{table}}}name": name})
        for values in rows:
            row = ET.SubElement(sheet, f"{{{table}}}table-row")
            for value in values:
                cell = ET.SubElement(row, f"{{{table}}}table-cell", {f"{{{office}}}value-type": "string"})
                if value is not None and value != "":
                    ET.SubElement(cell, f"{{{text}}}p").text = str(value)
    ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)


def column_name(index):
    name = ""
    while index:
        index, digit = divmod(index - 1, 26)
        name = chr(65 + digit) + name
    return name


def invoice_rows(source, count, repeated_header=False):
    rows = copy.deepcopy(source[:2])
    for sequence in range(1, count + 1):
        if repeated_header and sequence > 1 and (sequence - 1) % 31 == 0:
            rows.extend([[], copy.deepcopy(source[0]), copy.deepcopy(source[1])])
        row = copy.deepcopy(source[2])
        row[0] = sequence
        row[1] = f"26950000{sequence:012d}"
        row[4] = f"容量验证供应商{sequence:06d}"
        row[7] = row[8] = Decimal(amount_for(sequence)).scaleb(-2)
        row[9] = "蓝字"
        rows.append(row)
    total = sum(amount_for(i) for i in range(1, count + 1))
    footer = copy.deepcopy(source[-1])
    footer[7] = footer[8] = Decimal(total).scaleb(-2)
    rows.append(footer)
    return rows, {"count": count, "net_cents": total, "last_sequence": str(count), "last_number": f"26950000{count:012d}", "physical_rows": len(rows)}


def write_xlsx_fixture(path, source_content, rows):
    # 测试夹具保留原文件 A1:A1 范围异常，验证读取器不依赖该声明。
    namespace = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    root = ET.Element(f"{{{namespace}}}worksheet")
    ET.SubElement(root, f"{{{namespace}}}dimension", {"ref": "A1:A1"})
    data = ET.SubElement(root, f"{{{namespace}}}sheetData")
    for row_number, values in enumerate(rows, 1):
        row = ET.SubElement(data, f"{{{namespace}}}row", {"r": str(row_number)})
        for column, value in enumerate(values, 1):
            if value is None or value == "":
                continue
            cell = ET.SubElement(row, f"{{{namespace}}}c", {"r": f"{column_name(column)}{row_number}"})
            if isinstance(value, (int, float, Decimal)):
                ET.SubElement(cell, f"{{{namespace}}}v").text = str(value)
            else:
                cell.set("t", "inlineStr")
                inline = ET.SubElement(cell, f"{{{namespace}}}is")
                ET.SubElement(inline, f"{{{namespace}}}t").text = str(value)
    import io
    with zipfile.ZipFile(io.BytesIO(source_content)) as source, zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as target:
        sheet_path = next(name for name in source.namelist() if name.startswith("xl/worksheets/sheet") and name.endswith(".xml"))
        for entry in source.infolist():
            target.writestr(entry, ET.tostring(root, encoding="utf-8", xml_declaration=True) if entry.filename == sheet_path else source.read(entry.filename))
