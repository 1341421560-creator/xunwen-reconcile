from bisect import bisect_right
from .normalize import name_key
from .pdf_geometry import clusters, join_parts


def covered(intervals, left, right, tolerance):
    end = left
    for low, high in sorted(intervals):
        if low > end + tolerance:
            return False
        end = max(end, high)
        if end >= right - tolerance:
            return True
    return False


def extract_table(page, required, tolerance):
    parts, segments = page["parts"], page["segments"]
    expected = {name_key(value) for value in required}
    header_y = [y for y in clusters([p["y"] for p in parts if name_key(p["text"]) in expected], tolerance)
                if expected <= {name_key(p["text"]) for p in parts if abs(p["y"] - y) <= tolerance}]
    if len(header_y) != 1:
        raise ValueError(f"PDF 第 {page['number']} 页未识别到完整交通银行表头，暂不支持该版式或扫描图片")
    y = header_y[0]
    columns = clusters([(x1 + x2) / 2 for x1, y1, x2, y2 in segments if abs(x1 - x2) <= tolerance and min(y1, y2) < y < max(y1, y2)], tolerance)
    if len(columns) < len(required) + 1:
        raise ValueError(f"PDF 第 {page['number']} 页表格列边界不完整，无法可靠区分收支字段")
    left, right = columns[0], columns[-1]
    horizontal = [(min(x1, x2), max(x1, x2), (y1 + y2) / 2) for x1, y1, x2, y2 in segments if abs(y1 - y2) <= tolerance]
    boundaries = [line for line in clusters([h[2] for h in horizontal], tolerance)
                  if covered([(a, b) for a, b, row_y in horizontal if abs(row_y - line) <= tolerance], left, right, tolerance)]
    preceding = [line for line in boundaries if line < y]
    if not preceding:
        raise ValueError("PDF 表格缺少上边界")
    top = max(preceding)
    boundaries = [line for line in boundaries if line >= top]
    if len(boundaries) < 3 or not boundaries[0] < y < boundaries[1]:
        raise ValueError("PDF 表格行边界不完整")
    cells = [[[] for _ in columns[1:]] for _ in boundaries[1:]]
    for part in parts:
        row = bisect_right(boundaries, part["y"]) - 1
        column = bisect_right(columns, part["x"]) - 1
        if 0 <= row < len(cells) and 0 <= column < len(columns) - 1:
            cells[row][column].append(part)
    rows = [[join_parts(cell, tolerance) for cell in row] for row in cells]
    if not expected <= set(rows[0]) or len(rows[0]) != len(set(rows[0])):
        raise ValueError("PDF 表头缺失、重复或错列，整次导入取消")
    return {"headers": rows[0], "rows": rows[1:], "top": top, "bottom": boundaries[-1]}


def labeled_values(parts, names, tolerance):
    normalized = {name_key(label).rstrip(":") for label in names}
    labels = [p for p in parts if name_key(p["text"]).rstrip(":") in normalized]
    result = {}
    for label in labels:
        key = name_key(label["text"]).rstrip(":")
        if key in result:
            raise ValueError("PDF 页头或控制数存在重复标签")
        next_x = min((p["x"] for p in parts if p["x"] > label["x"] and abs(p["y"] - label["y"]) <= tolerance and p["text"].endswith((":", "："))), default=float("inf"))
        value = join_parts([p for p in parts if label["x"] < p["x"] < next_x and abs(p["y"] - label["y"]) <= tolerance], tolerance)
        if not value:
            raise ValueError(f"PDF {label['text']} 缺少对应内容")
        result[key] = value
    return result
