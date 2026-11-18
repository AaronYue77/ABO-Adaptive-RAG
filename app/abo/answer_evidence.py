"""Keep component properties and excluded accessories within their source scope."""

import json
import re

PARTS = {
    "手柄或把手": r"\bhandles?\b|手柄|把手|提手",
    "鞋底": r"\b(?:outsole|sole)s?\b|鞋底",
    "内衬": r"\blining\b|内衬",
    "肩带": r"\bstraps?\b|肩带|背带",
    "包装": r"\b(?:packaging|box)\b|包装",
    "图案": r"\b(?:print|pattern|logo)\b|图案|印花|标志",
}
ACCESSORIES = {
    "打气筒": (r"hand pump", r"打气筒|充气"),
    "电池": (r"batter(?:y|ies)", r"电池"),
    "充电器": (r"charger", r"充电器"),
    "电源适配器": (r"power adapter", r"电源适配器"),
}


def boundaries(fact):
    from catalog_constraints import COLORS, alias_pattern

    fields = fact.get("fields", {})
    text = json.dumps({"title": fact["title"], **fields}, ensure_ascii=False)
    notes = []
    for label, (english, _) in ACCESSORIES.items():
        if re.search(
            rf"\bwithout (?:an? )?(?:{english})\b|\b(?:{english}) (?:is |are )?not included\b",
            text,
            re.I,
        ):
            notes.append(
                {
                    "kind": "excluded_accessory",
                    "value": label,
                    "meaning": "不附带，不代表不需要或开箱即可使用",
                }
            )
    descriptions = fields.get("bullet_point", []) + fields.get("product_description", [])
    for chinese, english in COLORS.items():
        color = alias_pattern(english)
        if color.search(json.dumps(fields.get("color", []))):
            continue
        for description in descriptions:
            for clause in re.split(r"[.;!?。；]", str(description)):
                for match in color.finditer(clause):
                    for part, pattern in PARTS.items():
                        after = clause[match.end() :]
                        component = re.search(pattern, after, re.I)
                        gap = after[: component.start()] if component else ""
                        before = clause[: match.start()]
                        reverse = re.search(rf"(?:{pattern})\s+(?:is |are |in )?$", before, re.I)
                        if reverse or (
                            component
                            and len(gap.split()) <= 9
                            and not re.search(
                                r"\b(?:with|cup|mug|shoe|coat|bag|case|shirt|ball|chair|table)\b",
                                gap,
                                re.I,
                            )
                        ):
                            notes.append(
                                {
                                    "kind": "component_color",
                                    "value": chinese,
                                    "part": part,
                                    "quote": clause.strip(),
                                }
                            )
    return [dict(item) for item in dict.fromkeys(tuple(note.items()) for note in notes)][:8]


def validate_boundaries(answer, facts):
    from catalog_constraints import COLORS, alias_pattern

    evidence = json.dumps(
        [{"title": fact["title"], "fields": fact.get("fields", {})} for fact in facts],
        ensure_ascii=False,
    )
    for chinese, english in COLORS.items():
        if chinese not in evidence and not alias_pattern(english).search(evidence):
            for match in re.finditer(chinese, answer):
                if not re.search(
                    r"未|没|不|无法|不能|并非", answer[max(0, match.start() - 12) : match.start()]
                ):
                    raise ValueError(f"来源未说明{chinese}，用户偏好不能证明商品颜色")
    for fact in facts:
        for note in fact.get("source_boundaries", boundaries(fact)):
            if note["kind"] == "excluded_accessory":
                _, term = ACCESSORIES[note["value"]]
                for match in re.finditer(
                    rf"(?:无需|不用|不需).{{0,6}}(?:{term})|开箱即用|无需额外配件", answer
                ):
                    if not re.search(
                        r"不能|无法|不代表|不等于|未核实",
                        answer[max(0, match.start() - 12) : match.start()],
                    ):
                        raise ValueError(
                            f"原文仅说明不附带{note['value']}，不能声称不需要它、无需充气或开箱即用"
                        )
            else:
                part = PARTS[note["part"]]
                # A different product's explicit whole-item color can support that
                # value. Unbound mixed claims remain subject to the model review.
                if any(
                    alias_pattern(COLORS[note["value"]]).search(
                        json.dumps(other.get("fields", {}).get("color", []))
                    )
                    for other in facts
                ):
                    continue
                for clause in re.split(r"[，,、。；;！？\n]", answer):
                    if not re.search(part, clause, re.I):
                        for match in re.finditer(note["value"], clause):
                            prefix = clause[max(0, match.start() - 12) : match.start()]
                            if re.search(r"未|没|无法|不能|并非|不是|若需|想要|要求|寻找", prefix):
                                continue
                            raise ValueError(
                                f"{note['value']}的源句仅描述{note['part']}，不能扩大为整件颜色；请保留部位或说未明确整体颜色。源句：{note['quote']}"
                            )
        if re.search(r"\bMatch\b", fact["title"], re.I):
            for clause in re.split(r"[，,。；;！？\n]", answer):
                if (
                    (
                        fact["title"] in clause
                        or (fact.get("id") and re.search(rf"\b{re.escape(fact['id'])}\b", clause))
                    )
                    and "训练" in clause
                    and not re.search(r"不是|并非|不等于", clause)
                ):
                    raise ValueError("Match款不能与Training款合并说成训练款，请分别按商品原名介绍")
