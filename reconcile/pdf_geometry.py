import math


def point(x, y, matrix, box, rotation):
    left, bottom, right, top = map(float, box)
    raw_x = float(x) * matrix[0] + float(y) * matrix[2] + matrix[4] - left
    raw_y = float(x) * matrix[1] + float(y) * matrix[3] + matrix[5] - bottom
    width, height = right - left, top - bottom
    positions = {0: (raw_x, height - raw_y), 90: (raw_y, raw_x),
                 180: (width - raw_x, raw_y), 270: (height - raw_y, width - raw_x)}
    if rotation not in positions or not all(math.isfinite(v) for v in (*positions[rotation], width, height)):
        raise ValueError("PDF 页面坐标或旋转方向异常")
    return positions[rotation]


def clusters(values, tolerance):
    groups = []
    for value in sorted(values):
        if not groups or value - groups[-1][0] > tolerance:
            groups.append([value])
        else:
            groups[-1].append(value)
    return [sum(group) / len(group) for group in groups]


def join_parts(parts, tolerance):
    lines = clusters([p["y"] for p in parts], tolerance)
    text = ""
    for y in lines:
        line = "".join(p["text"] for p in sorted((p for p in parts if abs(p["y"] - y) <= tolerance), key=lambda p: p["x"]))
        if text and line and text[-1].isascii() and text[-1].isalpha() and line[0].isascii() and line[0].isalpha():
            text += " "
        text += line
    return text.strip()
