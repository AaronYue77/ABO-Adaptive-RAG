"""Check source scope across product types, including negative explanations."""

import pytest

from app.abo.service import bind_runtime

bind_runtime()
from app.abo.answer_evidence import boundaries, validate_boundaries  # noqa: E402


@pytest.mark.parametrize(
    "description,part,good,bad",
    [
        (
            "3D yellow vertical guitar shape staves as the handle",
            "手柄或把手",
            "黄色的是把手，杯身未说明。",
            "这是黄色杯子。",
        ),
        ("Red outsole", "鞋底", "红色的鞋底，鞋面未说明。", "这是红色鞋子。"),
        ("Blue lining", "内衬", "蓝色的是内衬。", "这是蓝色外套。"),
        ("Green strap", "肩带", "绿色肩带。", "这是绿色背包。"),
    ],
)
def test_component_color_does_not_become_whole_item_color(description, part, good, bad):
    facts = [{"title": "product", "fields": {"product_description": [description]}}]
    assert boundaries(facts[0])[0]["part"] == part
    validate_boundaries(good, facts)
    validate_boundaries("无法确认" + bad, facts)
    with pytest.raises(ValueError):
        validate_boundaries(bad, facts)


def test_whole_color_and_independent_explicit_color_are_not_component_claims():
    fact = {"title": "mug", "fields": {"bullet_point": ["Blue ceramic mug with cute handle"]}}
    assert boundaries(fact) == []
    validate_boundaries("这是蓝色杯子。", [fact])
    other = {"title": "other", "fields": {"color": ["Yellow"]}}
    fact["fields"]["bullet_point"] = ["Yellow handle"]
    validate_boundaries("另一款是黄色的。", [fact, other])
    assert boundaries(other) == []


@pytest.mark.parametrize(
    "source,bad,good",
    [
        (
            "Without Hand Pump",
            "开箱即用，无需充气。",
            "不附带打气筒，不能声称开箱即用，也不等于无需充气。",
        ),
        ("Batteries not included", "这款不用电池。", "未附带电池，需要另行核实使用要求。"),
        ("Without charger", "无需充电器。", "不附带充电器，无法确认无需充电器。"),
        ("Without power adapter", "不需电源适配器。", "不附带电源适配器。"),
    ],
)
def test_missing_accessory_cannot_become_no_need_for_accessory(source, bad, good):
    facts = [{"title": "product", "fields": {"style": [source]}}]
    assert boundaries(facts[0])[0]["kind"] == "excluded_accessory"
    validate_boundaries(good, facts)
    with pytest.raises(ValueError):
        validate_boundaries(bad, facts)


def test_group_claim_cannot_overwrite_an_individual_variant_name():
    facts = [{"id": "E2", "title": "Match Basketball", "fields": {}}]
    with pytest.raises(ValueError):
        validate_boundaries("E2、E3和E4都是训练款。", facts)
    validate_boundaries("E2是比赛款。训练款要另行比较。", facts)


def test_user_requested_color_does_not_supply_a_missing_source_color():
    facts = [{"title": "mug", "fields": {}, "requirements": {"color": "白色"}}]
    with pytest.raises(ValueError):
        validate_boundaries("这款是白色的。", facts)
    validate_boundaries("原文未说明白色。", facts)


def test_negative_statement_about_one_product_cannot_hide_another_color_claim():
    facts = [{"title": "mug", "fields": {"product_description": ["Yellow handle"]}}]
    with pytest.raises(ValueError):
        validate_boundaries("吉他杯是黄色，另两款未明确颜色。", facts)
    validate_boundaries("若需黄色杯身，仍需核实。", facts)
