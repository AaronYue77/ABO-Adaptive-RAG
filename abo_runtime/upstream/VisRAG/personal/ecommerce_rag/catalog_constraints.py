"""Parse explicit shopping constraints and verify them against original listing values."""

import json
import re
import unicodedata
from functools import lru_cache


COLORS = {
    "红色": "red", "黑色": "black", "白色": "white", "蓝色": "blue|navy", "绿色": "green",
    "粉色": "pink", "灰色": "gray|grey", "黄色": "yellow", "紫色": "purple",
    "橙色": "orange", "棕色": "brown", "银色": "silver", "金色": "gold",
}
MATERIALS = {
    "不锈钢": "stainless steel", "陶瓷": "ceramic|porcelain|stoneware", "玻璃": "glass",
    "木制": "wood|wooden", "纯棉": "100% cotton|pure cotton", "塑料": "plastic",
    "羊毛": "wool|woolen|woollen", "皮革": "leather", "尼龙": "nylon", "聚酯纤维": "polyester",
}
HEEL_QUERY = r"高跟鞋|高跟凉鞋|\b(?:high[ -]heel(?:ed|s)?|court shoes?|dress pumps?)\b"
HEEL_STYLE = re.compile(HEEL_QUERY+r"|\b(?:pumps?|stilettos?|escarpins?)\b|tacón alto", re.I)
LOW_HEEL = re.compile(r"平底|低跟|\b(?:flat|kitten|low|mid)[ -]heels?\b|\bflats\b", re.I)
FOOTWEAR = frozenset({"SHOES", "SANDAL", "BOOT", "TECHNICAL_SPORT_SHOE"})
SIZED_TYPES = FOOTWEAR | {"SHIRT", "PANTS", "DRESS", "SKIRT", "COAT", "SWEATER", "SHORTS",
                          "APPAREL", "BRA", "UNDERPANTS", "SWIMWEAR", "SOCKS", "HAT"}
SYSTEM = r"EU|EUR|US|UK|欧码|美码|英码"
NUMBER = r"\d{1,3}(?:\.\d+)?"
PREFIX_SIZE = re.compile(rf"(?<![A-Za-z0-9])(?P<system>{SYSTEM})[ -]?(?P<value>{NUMBER})(?![A-Za-z0-9.])", re.I)
SUFFIX_SIZE = re.compile(rf"(?<![\d.])(?P<value>{NUMBER})\s*(?:[A-E]\s+)?(?P<system>{SYSTEM})(?![A-Za-z])", re.I)
PLAIN_SIZE = re.compile(rf"(?<![\d.])(?P<value>{NUMBER}|XXXL|XXL|XL|XS|S|M|L)\s*(?:码|碼|号尺码)", re.I)
TITLE_SIZE = re.compile(rf"\b(?:size|talla|taille|größe)\s*[: -]?\s*(?P<value>{NUMBER}|XXXL|XXL|XL|XS|S|M|L)\b", re.I)
SIZE_FIELD = re.compile(rf"{NUMBER}|XXXL|XXL|XL|XS|S|M|L", re.I)
SYSTEMS = {"EUR": "EU", "欧码": "EU", "美码": "US", "英码": "UK"}
SIZE_LOOKUP = re.compile(r"尺码|鞋码|衣码|\b(?:shoe|clothing) size\b", re.I)
VARIANT_WORDS = re.compile(r"(?:还有|有没有|有无|有|是(?:不是)?|要|换成|换个|可选|(?:尺码|颜色)(?:为|是))", re.I)
STOCK_WORDS = re.compile(r"现货|库存(?!管理|系统)|在售|有货(?!架|柜|梯|车|运)|还能买|可以买到|今天.{0,12}能发")


@lru_cache(maxsize=64)
def alias_pattern(aliases):
    terms = [re.escape(term) for term in aliases.split("|")]
    return re.compile(r"(?<![A-Za-z0-9])(?:" + "|".join(terms) + r")(?![A-Za-z0-9])", re.I)


def sizes(text, *, source=False, explicit=False):
    """Return size system, value and verbatim source text; keep sizing systems distinct."""
    text = unicodedata.normalize("NFKC", text)
    result = []
    spans = []
    for pattern in (PREFIX_SIZE, SUFFIX_SIZE, PLAIN_SIZE, *((TITLE_SIZE,) if source else ())):
        for match in pattern.finditer(text):
            if any(match.start() < end and match.end() > start for start, end in spans):
                continue
            spans.append(match.span())
            system = (match.groupdict().get("system") or "").upper()
            result.append((SYSTEMS.get(system, system), match["value"].upper(), match.group()))
    if not result and explicit and SIZE_FIELD.fullmatch(text.strip()):
        result.append(("", text.strip().upper(), text.strip()))
    return result


def size_matches(available, requested):
    system, value = requested
    return any(actual == value and (not system or system == scale) for scale, actual, *_ in available)


def field_texts(raw, field, *, standardized=False):
    for entry in raw.get(field) or []:
        if not isinstance(entry, dict):
            continue
        if entry.get("value"):
            yield str(entry["value"])
        if standardized:
            yield from (value for value in entry.get("standardized_values") or [] if isinstance(value, str))


def size_evidence(raw, title, kind):
    entries = [(system, value, quote, "size") for text in field_texts(raw, "size")
               for system, value, quote in sizes(text, source=True, explicit=True)]
    if kind in SIZED_TYPES:
        entries += [(system, value, quote, "item_name")
                    for system, value, quote in sizes(title, source=True)]
    return list(dict.fromkeys(entries))


def named_constraints(text):
    """Only enforce positively specified constraints with unambiguous meanings."""
    result = {}
    for key, names in (("color", COLORS), ("material", MATERIALS)):
        labels = [label for label in names if label in text]
        negative = [label for label in labels if re.search(r"(?:不要|排除|拒绝|非)\s*"+label+"|"+label+r"(?:除外|以外)",text)]
        if negative:
            result['exclude_'+key] = negative
        labels = [label for label in labels if label not in negative]
        if len(labels) == 1:
            result[key] = labels[0]
        elif key == "color" and len(labels) > 1 and re.search(r"或者|或|任一", text):
            result[key] = labels
        elif len(labels) > 1:
            result['ambiguous'] = True
    requested = list(dict.fromkeys((system, value) for system, value, _ in sizes(text)))
    if len(requested) == 1:
        result["size"] = list(requested[0])
    elif len(requested) > 1:
        result['ambiguous'] = True
    return result


def remove_constraints(text, criteria):
    for key in ("color", "material", "exclude_color", "exclude_material"):
        found = criteria.get(key, [])
        for label in ([found] if isinstance(found, str) else found):
            text = re.sub(r"(?:(?:不要|排除|拒绝|非)\s*)?"+re.escape(label)+r"(?:除外|以外)?", " ", text)
    if "size" in criteria:
        for _, _, quote in sizes(text):
            text = text.replace(quote, " ")
    return re.sub(r"(?:^|\s)(?:或者|或|任一)(?=\s|$)|[，,、]", " ", text).strip()


def refine_constraints(text):
    criteria = named_constraints(text)
    if not criteria or not re.match(r'(?:请问|那|请)?\s*(?:只看|改成|筛选|有没有|还有|有|换成|只要)',text):
        return None
    remainder = remove_constraints(text, criteria)
    remainder = re.sub(r'请问|那|请|只看|改成|筛选|有没有|还有|有|换成|只要|的|吗|呢|吧|[\s。？！?!]', '', remainder)
    return criteria if not remainder else None


def matches_criteria(title, kind, raw_json, criteria_json):
    criteria = json.loads(criteria_json)
    if criteria.get('ambiguous'):
        return False
    raw = json.loads(raw_json)
    if criteria.get("style") == "high_heel":
        styles = (title, *field_texts(raw, "style"))
        if any(LOW_HEEL.search(value) for value in styles) or not any(HEEL_STYLE.search(value) for value in styles):
            return False
    for key, names, fields in (("color", COLORS, ("color",)), ("material", MATERIALS, ("material", "fabric_type"))):
        label = criteria.get(key)
        excluded = criteria.get('exclude_'+key, [])
        if not (label or excluded):
            continue
        choices = [label] if isinstance(label, str) else (label or [])
        values = [value for field in fields for value in field_texts(raw, field)]
        # A normalized label must not turn an explicit Red listing into a Black match.
        if not values and key=='color':
            values=list(field_texts(raw,'color',standardized=True))
        def includes(value, source):
            match = alias_pattern(names[value]).search(source)
            return value in source or (match and not re.match(r'[- ]free\b',source[match.end():],re.I))

        if choices and not any(includes(value,source) for value in choices for source in (values or [title])):
            return False
        if excluded and (not any(includes(value,source) for value in names for source in values) or
                         any(includes(value,source) for value in excluded for source in values)):
            return False
    return "size" not in criteria or size_matches(size_evidence(raw, title, kind), criteria["size"])
