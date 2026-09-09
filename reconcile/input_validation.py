import json
import re


def boolean_field(payload, key, default=False):
    value = payload.get(key, default)
    if type(value) is not bool:
        raise ValueError(f"{key} 必须为布尔值")
    return value


def month_field(payload):
    value = payload.get("month", "")
    if not isinstance(value, str) or (value and not re.fullmatch(r"[0-9]{4}-(0[1-9]|1[0-2])", value)):
        raise ValueError("付款月份必须使用 YYYY-MM 格式")
    return value


def json_object(content):
    def unique_pairs(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"JSON 字段重复：{key}")
            result[key] = value
        return result

    def invalid_constant(value):
        raise ValueError(f"JSON 不支持数值 {value}")

    result = json.loads(content, object_pairs_hook=unique_pairs, parse_constant=invalid_constant)
    if not isinstance(result, dict):
        raise ValueError("请求必须为 JSON 对象")
    return result
