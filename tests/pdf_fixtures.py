import base64
import io
import json
from pypdf import PdfWriter
from pypdf.generic import ArrayObject, DecodedStreamObject, DictionaryObject, NameObject, NumberObject


HEADERS = ["序号", "交易日期", "借方发生额", "贷方发生额", "余额", "对方户名", "摘要", "流水号"]


def payment(reference="PDF0001", amount=1200000, party="测试供应商", summary="货款", income=False):
    return {"reference": reference, "amount": amount, "party": party, "summary": summary, "income": income}


def statement(company, transactions=None, per_page=8, account="443000000000000000001"):
    transactions = transactions if transactions is not None else [payment(), payment("PDF0002", 2500000, "测试客户", income=True)]
    controls = {}
    for incoming, side in ((False, "借方"), (True, "贷方")):
        selected = [r for r in transactions if r["income"] == incoming]
        for scope in ("当前账单", "本月累计"):
            controls[scope + side + "发生数"] = str(len(selected))
        controls["本月累计" + side + "发生额"] = f"{sum(r['amount'] for r in selected) / 100:.2f}"
    pages, balance = [], 300000000
    for offset in range(0, len(transactions), per_page):
        number = len(pages) + 1
        rows = [["承前" if number == 1 else "承上页", "", "", "", f"{balance / 100:.2f}", "", "", ""]]
        for sequence, item in enumerate(transactions[offset:offset + per_page], offset + 1):
            balance += item["amount"] if item["income"] else -item["amount"]
            amount = f"{item['amount'] / 100:.2f}"
            rows.append([str(sequence), "20260803", "" if item["income"] else amount, amount if item["income"] else "",
                         f"{balance / 100:.2f}", item["party"], item["summary"], item["reference"]])
        pages.append({"title": "交通银行测试分行明细对账单", "identity": {"账号": account, "户名": company, "币种": "人民币", "年份": "2026", "月份": "08", "页码": f"本月第1份-第{number}页"},
                      "headers": list(HEADERS), "rows": rows, "controls": controls if offset + per_page >= len(transactions) else {}})
    return pages


def pdf_bytes(pages, rotation=0, encrypted=False, draw_grid=True):
    # 构造带中文映射的内存 PDF 夹具，测试无需额外字体文件或用户的真实账单。
    writer = PdfWriter()
    letters = set(json.dumps(pages, ensure_ascii=False)) | set("：")
    mapping = "\n".join(f"<{ord(c):04X}> <{c.encode('utf-16-be').hex()}>" for c in sorted(letters))
    cmap = DecodedStreamObject()
    cmap.set_data((f"/CIDInit /ProcSet findresource begin 12 dict begin begincmap /CIDSystemInfo << /Registry (Adobe) /Ordering (UCS) /Supplement 0 >> def /CMapName /Test def /CMapType 2 def 1 begincodespacerange <0000> <FFFF> endcodespacerange {len(letters)} beginbfchar\n{mapping}\nendbfchar endcmap CMapName currentdict /CMap defineresource pop end end").encode("ascii"))
    descendant = DictionaryObject({NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/CIDFontType0"), NameObject("/BaseFont"): NameObject("/STSong-Light"), NameObject("/DW"): NumberObject(500)})
    font = DictionaryObject({NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type0"), NameObject("/BaseFont"): NameObject("/STSong-Light"), NameObject("/Encoding"): NameObject("/Identity-H"), NameObject("/DescendantFonts"): ArrayObject([writer._add_object(descendant)]), NameObject("/ToUnicode"): writer._add_object(cmap)})
    font_ref = writer._add_object(font)
    transforms = {0: "1 0 0 1 0 0", 90: "0 1 -1 0 595 0", 180: "-1 0 0 -1 842 595", 270: "0 -1 1 0 0 842"}
    for spec in pages:
        page = writer.add_blank_page(width=595 if rotation % 180 else 842, height=842 if rotation % 180 else 595)
        page.rotate(rotation)
        page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F1"): font_ref})})
        commands = ["q " + transforms[rotation] + " cm"]
        def put(x, y, text, size=7):
            for line_number, line in enumerate(str(text).split("\n")):
                commands.append(f"q 1 0 0 1 {x} {595-y-line_number*8} cm BT /F1 {size} Tf 1 0 0 1 0 0 Tm <{line.encode('utf-16-be').hex()}> Tj ET Q")
        def line(x1, y1, x2, y2):
            commands.append(f"{x1} {595-y1} m {x2} {595-y2} l S")
        put(250, 40, spec["title"], 12)
        for key, x, y, value_x in (("账号",20,90,55),("户名",280,90,315),("币种",20,70,55),("年份",180,70,215),("月份",290,70,325),("页码",400,70,435)):
            if key in spec["identity"]:
                put(x, y, key + "："); put(value_x, y, spec["identity"][key])
        columns = [20, 45, 110, 190, 270, 350, 500, 670, 822]
        boundaries = [116, 132] + [148 + index * 32 for index in range(len(spec["rows"]))]
        if draw_grid:
            for x in columns:
                line(x, boundaries[0], x, boundaries[-1])
            for y in boundaries:
                line(columns[0], y, columns[-1], y)
        for x, label in zip(columns, spec["headers"]):
            put(x + 1, 125, label)
        for index, row in enumerate(spec["rows"]):
            for x, value in zip(columns, row):
                if value:
                    put(x + 1, boundaries[index + 1] + 9, value)
        for index, (label, value) in enumerate(spec["controls"].items()):
            y = boundaries[-1] + 14 + index // 2 * 14
            x = 20 if index % 2 == 0 else 440
            put(x, y, label + "："); put(x + 145, y, value)
        commands.append("Q")
        stream = DecodedStreamObject()
        stream.set_data("\n".join(commands).encode("ascii"))
        page[NameObject("/Contents")] = writer._add_object(stream)
    if encrypted:
        writer.encrypt("test-password")
    result = io.BytesIO()
    writer.write(result)
    return result.getvalue()


def pdf_upload(pages, **options):
    return {"name": "交通银行测试.pdf", "content": base64.b64encode(pdf_bytes(pages, **options)).decode("ascii")}
