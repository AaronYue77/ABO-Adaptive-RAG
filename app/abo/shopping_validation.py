"""Regression checks for common shopping turns against the full ABO catalog."""

import json

PUMPS = "B07WFNPGCM@amazon.com.au"
FAMILY = "B07WGNM6NZ@amazon.com.au"
FAN = "B07SSCD71B@amazon.com.au"

SCENARIOS = (
    (
        "catalog totals and browsing",
        (
            ("你好，请问手表类的商品有多少个？", "catalog_count", "28 条"),
            ("列出这些商品", "search_results", "手表"),
            ("美国站有多少款手表？", "catalog_count", "3 条"),
            ("列出这些商品", "search_results", "手表"),
            ("整个库一共有多少商品？", "catalog_count", "147,702 条"),
            ("ABO一共有多少个类目？", "catalog_count", "576 个"),
            ("COFFEE 有多少条记录？", "catalog_count", "293 条"),
            ("虚构的悬浮矿灯类商品有多少个？", "clarify", "ABO 类目"),
            ("手表现在有多少款在售？", "requires_tool", "库存"),
        ),
    ),
    (
        "filtering and changing requirements",
        (
            ("搜索红色高跟鞋", "search_results", "颜色、款式"),
            ("只看39码的", "not_found", "同时满足"),
            ("改成黑色", "search_results", "尺码"),
            ("换成38码", "search_results", "尺码"),
            ("搜索黑色双肩包", "search_results", "颜色"),
            ("换成蓝色", "search_results", "颜色"),
            ("黑色或白色的双肩包有多少款？", "catalog_count", "条"),
            ("搜索双肩包不要黑色", "search_results", "颜色排除"),
            ("搜索黑色和白色双肩包", "clarify", "任选"),
            ("搜索不锈钢咖啡杯", "search_results", "材质"),
            ("请帮我比较前两款吗", "clarify", "差异"),
            ("第三十款", "clarify", "序号"),
        ),
    ),
    (
        "listing size and same-model variants",
        (
            (PUMPS + " 请问这款尺码是多少？", "answer", "6 us"),
            ("完整展示", "answer", "标题标注尺码"),
            ("这双有 6 US 的吗？", "answer", "包含你问的尺码"),
            ("请问有39码的吗？", "partial", "暂未找到"),
            ("这双现在有39码的库存吗？", "requires_tool", "当前库存"),
            (FAMILY + " 有 6 US 的吗？", "answer", "同站点另有"),
            ("第一款", "selected", "接下来就看"),
            ("这双什么尺码？", "answer", "6 us"),
        ),
    ),
    (
        "available fields and unavailable business data",
        (
            (FAN + " 的颜色和库存", "partial", "White"),
            ("这款品牌是什么？", "answer", "AmazonBasics"),
            ("那价格呢？", "requires_tool", "售价"),
            ("给我写 Python 代码统计手表数量", "out_of_scope", "服务范围"),
            ("请问有39码的吗？", "clarify", "想查哪款"),
        ),
    ),
)


def verify_candidates(session, row):
    from catalog_constraints import matches_criteria

    criteria = row["retrieval_trace"].get("required_source_criteria")
    if not criteria or row["decision"] != "search_results":
        return True
    for item in row["catalog_items"]:
        (source,) = session.app.retriever.get(item["sku"], item["domain"])
        if not matches_criteria(
            source["title"], source["product_type"], source["raw_json"], json.dumps(criteria)
        ):
            return False
    return bool(row["catalog_items"])


def run_shopping_validation(session, path):
    checks = []
    for scenario, turns in SCENARIOS:
        session.respond("重置")
        for question, expected, required in turns:
            row = session.respond(question)
            answer = row["display_answer"]
            passed = row["decision"] == expected and required.casefold() in answer.casefold()
            if expected in {"catalog_count", "answer", "partial"}:
                passed &= bool(row["citations"]) and bool(row["retrieved_pages"])
            if expected in {"out_of_scope", "requires_tool"}:
                passed &= not row["citations"]
            if question == "列出这些商品" and checks[-1]["question"].startswith("美国站"):
                passed &= len(row["catalog_items"]) == 3 and all(
                    item["domain"] == "amazon.com" for item in row["catalog_items"]
                )
            if question == PUMPS + " 请问这款尺码是多少？":
                passed &= row["current_listing_keys_after"] == [PUMPS]
                passed &= any(citation.endswith(":item_name") for citation in row["citations"])
            if question == FAMILY + " 有 6 US 的吗？":
                passed &= row["current_listing_keys_after"] == [FAMILY]
                passed &= PUMPS in [item["listing_key"] for item in row["catalog_items"]]
            if question == FAN + " 的颜色和库存":
                passed &= "库存" in answer and all(
                    citation.endswith(":color") for citation in row["citations"]
                )
            passed &= verify_candidates(session, row)
            checks.append(
                {
                    "scenario": scenario,
                    "question": question,
                    "expected": expected,
                    "actual": row["decision"],
                    "answer": answer,
                    "citations": row["citations"],
                    "passed": bool(passed),
                }
            )
    result = {
        "cases": len(checks),
        "passed": sum(check["passed"] for check in checks),
        "checks": checks,
    }
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", "utf-8")
    assert result["cases"] == result["passed"], [check for check in checks if not check["passed"]]
    return result
