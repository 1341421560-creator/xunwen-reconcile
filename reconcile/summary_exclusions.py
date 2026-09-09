def normalize_keywords(value):
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ValueError("自定义摘要排除关键词必须是字符串列表")
    # 首尾空白和重复项不参与匹配，保留关键词内部的原始字符。
    return list(dict.fromkeys(item.strip() for item in value if item.strip()))


def summary_rule_groups(settings, config):
    built_in = config["exclude_keywords"] if settings.get("exclude_special", True) else []
    custom = normalize_keywords(settings.get("custom_exclude_keywords", []))
    return (("摘要命中：", built_in), ("自定义摘要命中：", custom))


def exclusion_reason(bank, allocated_cents, groups):
    if bank["direction"] == "收入":
        return "收入不参与进项发票比对"
    if bank.get("manual_exclusion"):
        return bank["manual_exclusion"]
    if allocated_cents:
        return ""
    for prefix, keywords in groups:
        hit = next((word for word in keywords if word and word in bank["summary"]), None)
        if hit is not None:
            return prefix + hit
    return ""
