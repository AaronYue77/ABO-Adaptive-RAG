"""Use original listing fields and actual photo pages for different evidence needs."""
import json
import re
from decimal import Decimal

from full_cards import choose_pages, evidence_fields


VERSION = "source-evidence-v3"
FIELDS = {
    "brand": ("品牌", "brand"),
    "color": ("颜色", "color"),
    "material": ("材质", "材料", "material"),
    "model_number": ("型号", "model_number"),
    "width": ("宽度", "多宽", "width"),
    "height": ("高度", "多高", "height"),
    "length": ("长度", "多长", "length"),
    "weight": ("重量", "有多重", "多重", "weight"),
}
ALIASES = {alias: field for field, aliases in FIELDS.items() for alias in aliases}
FIELD_NAMES = "|".join(sorted(ALIASES, key=len, reverse=True))
FIELD_REQUEST = re.compile(
    rf"(?P<fields>(?:{FIELD_NAMES})(?:(?:以及|和|与|、)(?:{FIELD_NAMES}))*)"
    r"\s*(?:字段)?\s*(?:分别)?\s*(?:原文|原始值|数值|值)?\s*"
    r"(?:是什么|是多少|有多少|多少|是什么样的|呢|吗)?",
    re.I,
)
LISTING_REF = re.compile(
    r"(?<![A-Z0-9])(?:B[0-9][A-Z0-9]{8}|[0-9]{9}[0-9X])"
    r"(?:@[a-z0-9./-]+)?(?![A-Z0-9])",
    re.I,
)
PHOTO_REQUEST = re.compile(
    r"图片|照片|原图|外观|图案|photo|image|appearance|侧面.{0,12}(?:口袋|侧袋|水壶袋)|"
    r"(?:有没有|有无|有)\s*(?:水壶袋|侧袋|扶手)",
    re.I,
)


LOOKUP_PREFIX = re.compile(
    r"^(?:(?:请问|麻烦|请|帮我|替我|先|再|另外|并且|以及|接着)\s*)*"
    r"(?:(?:告诉我|给我|查一下|查询|查看|查|看看|列出|我想知道)\s*)?"
    r"(?:这(?:个|款|件)(?:商品)?\s*)?"
)


def requested_fields(question):
    """Recognize plain source lookups without interpreting units or comparisons."""
    text = re.sub(r"[“”‘’「」『』]", "", question.strip())
    text = re.sub(r"[,，。！!\s]+(?:谢谢(?:你)?|多谢)[。！!\s]*$", "", text)
    text = LOOKUP_PREFIX.sub("", text.rstrip("？?。！! "), count=1)
    refs = list(LISTING_REF.finditer(text))
    if refs:
        if text[:refs[0].start()].strip():
            return ()
        if any(not re.fullmatch(r"\s*(?:和|与|、|,|，)\s*", text[a.end():b.start()])
               for a, b in zip(refs, refs[1:])):
            return ()
        text = text[refs[-1].end():].lstrip()
    else:
        text = re.sub(r"^(?:那么|那)?(?:这(?:两款|两个|两件|款|件|双|把|个商品)|它|该商品|所选商品)?",
                      "", text).lstrip()
    text = re.sub(r"^(?:的\s*)?(?:商品资料里记录的\s*)?", "", text)
    match = FIELD_REQUEST.fullmatch(text)
    if not match:
        return ()
    return tuple(dict.fromkeys(ALIASES[name.casefold()] for name in
                               re.split(r"以及|和|与|、", match["fields"])))


def split_photo_request(question):
    """Split a plain field lookup followed by a photo question about the same item."""
    parts = re.split(r"[，,；;？?]\s*|以及", question, maxsplit=1)
    if len(parts) == 2 and PHOTO_REQUEST.search(parts[1]) and not LISTING_REF.search(parts[1]):
        fields = requested_fields(parts[0])
        if fields:
            return fields, parts[1]
    return (), question


def plan_field_lookups(question):
    """Split two or three explicit, independently verifiable catalog lookups."""
    parts = re.split(r"[；;]\s*|[，,]\s*(?=再|另外|并且|以及|接着)", question)
    if not 2 <= len(parts) <= 3:
        return []
    plan = []
    for part in parts:
        references = LISTING_REF.findall(part)
        fields = requested_fields(part)
        if len(references) != 1 or not fields:
            return []
        sku, _, domain = references[0].rstrip(".").partition("@")
        plan.append((sku.upper(), domain.lower(), fields))
    return plan


def read_fields(products, fields):
    answers, claims, evidence = [], [], []
    missing = 0
    for product in products:
        key = product["listing_key"]
        values = evidence_fields(json.loads(product["raw_json"]))
        for field in fields:
            actual = list(dict.fromkeys(value.strip() for name, value in values
                                        if name == field and value.strip()))
            citation = f"{key}:metadata:{field}"
            label = FIELDS[field][0]
            if not actual:
                missing += 1
                answers.append(f"{key} 的{label}在这条商品记录中未报告。")
                continue
            source = {"member": product["source_member"], "line": product["source_line"],
                      "sha256": product["raw_sha256"]}
            claims.append({"listing_key": key, "field": field, "values": actual,
                           "citation": citation, "source": source})
            evidence.append({"page_id": citation, "doc_id": key, "page": None,
                             "sku": product["sku"], "listing_key": key,
                             "rank": len(evidence) + 1, "score": 1.0,
                             "text_source": "original_metadata_field", "source": source,
                             "field": field})
            rendered = " / ".join(actual)
            if len(actual) > 1:
                rendered += "（源记录有多个值）"
            answers.append(f"{key} 的{label}为 {rendered}。")
    return {
        "action": "answer" if claims else "not_found",
        "answer": "\n".join(answers),
        "answer_source": "original_metadata_fields",
        "claims": claims,
        "citations": [claim["citation"] for claim in claims],
        "retrieved_pages": evidence,
        "retrieval_trace": {"requested_fields": list(fields), "missing_fields": missing},
    }


COMPARE_AXES = {
    "重":("weight","max"), "轻":("weight","min"), "宽":("width","max"), "窄":("width","min"),
    "高":("height","max"), "矮":("height","min"), "长":("length","max"), "短":("length","min"),
}
COMPARABLE_UNITS = {
    "weight":{"gram":Decimal("1"),"kilogram":Decimal("1000"),
              "pound":Decimal("453.59237"),"ounce":Decimal("28.349523125")},
    "dimension":{"millimeter":Decimal("1"),"centimeter":Decimal("10"),"meter":Decimal("1000"),
                 "inch":Decimal("25.4"),"inches":Decimal("25.4"),
                 "foot":Decimal("304.8"),"feet":Decimal("304.8")},
}


def comparison_axis(question):
    match = re.search(r"(?:更|比较)([重轻宽窄高矮长短])(?:一点|一些|些|呢|吗)?[？！?!。\s]*$", question)
    if match:
        return COMPARE_AXES[match[1]]
    same = re.search(rf"({FIELD_NAMES})(?:是否|是不是|有|是|都)?(?:一样|相同|差异|区别)(?:吗|呢)?[？！?!。\s]*$", question,re.I)
    if same:
        return ALIASES[same[1].casefold()], "equal"
    return None


def compare_source_fields(products, axis):
    field, operation = axis
    result = read_fields(products,(field,))
    claims = result["claims"]
    if len(claims)!=2 or any(len(claim["values"])!=1 for claim in claims):
        result["answer"] += "\n两个商品都需要有明确可比的源值，现有资料不足以判断。"
        result["action"] = "partial" if claims else "not_found"
        return result
    values = [claim["values"][0].strip() for claim in claims]
    if field in {"weight","width","height","length"}:
        unit_map = COMPARABLE_UNITS["weight" if field=="weight" else "dimension"]
        measurements = [re.fullmatch(r"(\d+(?:\.\d+)?)\s+([A-Za-z]+)",value) for value in values]
        if any(not item or item[2].casefold() not in unit_map and item[2].casefold().rstrip("s") not in unit_map
               for item in measurements):
            result.update(action="partial",answer=result["answer"]+"\n这两个原始值的单位不足以自动比较。")
            return result
        normalized = []
        for match in measurements:
            unit = match[2].casefold()
            normalized.append(Decimal(match[1]) * unit_map[unit if unit in unit_map else unit.rstrip("s")])
    else:
        normalized = [value.casefold() for value in values]
    if normalized[0] == normalized[1]:
        conclusion = "这两条来源记录的该项相同。"
    elif operation == "equal":
        conclusion = "这两条来源记录的该项不同。"
    else:
        index = normalized.index((max if operation == "max" else min)(normalized))
        conclusion = f"按已收录的{FIELDS[field][0]}对比，符合你所问条件的是 {claims[index]['listing_key']}。"
    result["answer"] += "\n"+conclusion
    return result


def fields_alongside_price(question):
    """Retain independently resolvable fields in a plain field-plus-price list."""
    price = r"(?:价格|售价|原价|到手价|成交价|折后价)"
    connector = r"(?:以及|和|与|、)"
    without_price = re.sub(rf"{connector}\s*{price}|{price}\s*{connector}", "", question, count=1)
    return requested_fields(without_price) if without_price != question else ()


def guard_identity_echo(result, products, question):
    """Reject an identifier-only answer to a question about the item's evidence."""
    if result["action"] != "answer" or re.search(r"编号|货号|\bASIN\b|\bSKU\b", question, re.I):
        return result, None
    answer = result["answer"].strip(" \t\r\n。.!！“”\"'").casefold()
    if not any(answer in {product["sku"].casefold(), product["listing_key"].casefold()}
               for product in products):
        return result, None
    return {**result, "action": "not_found",
            "answer": "已找到这款商品，现有证据回答未能核实你问的内容。可以指定要查的参数或图片位置。",
            "citations": []}, {"reason": "identity_only_answer", "rejected_answer": result["answer"],
                               "rejected_citations": result["citations"]}


def photo_pages(document, question):
    pages = choose_pages(document, question, budget=len(document["pages"]))
    return [page for page in pages if page["photo_page"]][:1]


def alternate_pages(documents, first, question, budget):
    """Replace at most one page with a genuinely new, query-matching page."""
    existing = {page["page_id"] for page in first}
    for doc in documents:
        for page in choose_pages(doc, question, budget=len(doc["pages"])):
            if page["page_id"] in existing or page["score"] <= 0:
                continue
            same_product = [item for item in first if item["listing_key"] == page["listing_key"]]
            if len(first) < budget:
                return [*first, page]
            if same_product:
                replaced = min(same_product, key=lambda item: item["score"])
                return [page if item["page_id"] == replaced["page_id"] else item for item in first]
    return []
