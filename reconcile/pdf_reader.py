import io
from pypdf import PdfReader
from pypdf.errors import PyPdfError
from .pdf_geometry import point


def page_geometry(page, limits, number):
    parts, segments, pending = [], [], []
    current = start = None

    def coordinates(x, y, cm):
        # 保留原始内容流与字体映射，只转换提取出的坐标，避免重写旋转页面导致文字变化。
        return point(x, y, cm, page.mediabox, page.rotation % 360)

    def text_visitor(text, cm, tm, font, size):
        if not text.strip():
            return
        if any(ord(c) < 32 and c not in "\n\r\t" for c in text) or "\ufffd" in text:
            raise ValueError(f"PDF 第 {number} 页文字编码无法可靠识别，请重新导出电子对账单")
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        if len(lines) != 1:
            raise ValueError(f"PDF 第 {number} 页含无法定位的多行文字，请使用银行原始电子对账单")
        x, y = coordinates(tm[4], tm[5], cm)
        parts.append({"text": lines[0], "x": x, "y": y})
        if len(parts) > limits["max_text_parts_per_page"]:
            raise ValueError("PDF 单页文字数量超过上限，请拆分文件")

    def operand(op, args, cm, tm):
        nonlocal current, start
        if op == b"m":
            current = start = coordinates(args[0], args[1], cm)
        elif op == b"l" and current is not None:
            target = coordinates(args[0], args[1], cm)
            pending.append((*current, *target))
            current = target
        elif op == b"re":
            x, y, width, height = map(float, args)
            corners = [coordinates(a, b, cm) for a, b in ((x, y), (x + width, y), (x + width, y + height), (x, y + height))]
            pending.extend((*corners[i], *corners[(i + 1) % 4]) for i in range(4))
            current = start = corners[0]
        elif op in (b"h", b"s", b"b", b"b*") and current is not None and start is not None:
            pending.append((*current, *start))
            current = start
        elif op in (b"c", b"v", b"y"):
            current = None
        if op in (b"S", b"s", b"B", b"B*", b"b", b"b*"):
            segments.extend(pending)
        if op in (b"S", b"s", b"B", b"B*", b"b", b"b*", b"f", b"F", b"f*", b"n"):
            pending.clear()
            current = start = None
        if len(segments) + len(pending) > limits["max_path_segments_per_page"]:
            raise ValueError("PDF 单页表格路径过于复杂，请拆分文件或使用原始电子对账单")

    page.extract_text(visitor_text=text_visitor, visitor_operand_before=operand)
    if not parts:
        raise ValueError(f"PDF 第 {number} 页没有可读取文字，暂不支持扫描图片，请导出银行电子 PDF 或 Excel")
    return {"number": number, "parts": parts, "segments": segments}


def read_pdf_pages(content, limits):
    if not content.startswith(b"%PDF-"):
        raise ValueError("文件内容不是 PDF，请使用银行导出的原始电子对账单")
    try:
        reader = PdfReader(io.BytesIO(content), strict=True)
        if reader.is_encrypted:
            raise ValueError("暂不支持加密 PDF，请从银行重新导出可直接打开的电子对账单")
        if not 1 <= len(reader.pages) <= limits["max_pages"]:
            raise ValueError(f"PDF 必须包含 1 至 {limits['max_pages']} 页，整次导入取消")
        pages, total = [], 0
        for number, page in enumerate(reader.pages, 1):
            stream = page.get_contents()
            size = len(stream.get_data()) if stream is not None else 0
            total += size
            if size > limits["max_stream_bytes_per_page"] or total > limits["max_stream_bytes_total"]:
                raise ValueError("PDF 解压后的页面内容超过上限，请拆分文件")
            pages.append(page_geometry(page, limits, number))
        return pages
    except (PyPdfError, KeyError, TypeError, IndexError, OverflowError, RecursionError) as exc:
        raise ValueError("PDF 损坏、内容不完整或结构不受支持，请从银行重新导出；整次导入取消") from exc
