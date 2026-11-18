"""Cross-category conversation regression against real ABO source records."""

import json
from pathlib import Path

FAN = "B07SSCD71B@amazon.com.au"
COFFEE = "B07Z6DLFQS@amazon.com"
OTHER = "B083V9KNTS@amazon.com"

# Each case specifies the expected route and a source or explanation marker.
SCENARIOS = (
    (
        "fan-to-catalog",
        (
            ("我想查询电风扇这类商品", "search_results", "ELECTRIC_FAN"),
            (FAN, "selected", FAN),
            ("帮我看一下这个属性" + FAN, "answer", FAN),
            ("还有呢给我完整的展示出来", "answer", "45 W"),
            ("还有什么", "answer", "展示完"),
            ("商品列表", "catalog", "147,702"),
            ("下一页", "catalog", "接着上次目录"),
            ("回到第一页", "catalog", "手机壳"),
            ("这款电风扇的品牌是什么", "answer", "AmazonBasics"),
        ),
    ),
    (
        "identify-compare",
        (
            ("我想查咖啡这类商品", "search_results", "COFFEE"),
            ("请问 " + COFFEE + "和" + OTHER + "哪一个更贵", "requires_tool", "可核实的价格"),
            ("第一款和第二款哪个更便宜", "requires_tool", "可核实的价格"),
            (COFFEE + " 和 " + OTHER + " 的品牌一样吗", "answer", "相同"),
            (COFFEE + " 和 " + OTHER + " 哪款更重", "answer", "按已收录"),
            (COFFEE + " 的重量和价格", "partial", "可核实的价格"),
            ("Which is cheaper, B07Z6DLFQS or B083V9KNTS?", "requires_tool", "可核实的价格"),
        ),
    ),
    (
        "select-page-switch",
        (
            ("鞋子", "search_results", "SHOES"),
            ("第一款", "selected", "已选择"),
            ("全部参数", "answer", "已收录"),
            ("下一页", "answer", "展示完"),
            ("椅子", "search_results", "CHAIR"),
            ("第二款", "selected", "已选择"),
            ("把剩下的都列出来", "answer", "已收录"),
            ("耳机", "search_results", "HEADPHONES"),
            ("第一款", "selected", "已选择"),
            ("再详细一点", "answer", "商品"),
        ),
    ),
    (
        "catalog-vs-scope",
        (
            ("宠物用品", "search_results", "PET_SUPPLIES"),
            ("我想查询化妆品", "search_results", "BEAUTY"),
            ("台灯", "search_results", "LAMP"),
            ("为什么白天能看到月亮", "out_of_scope", "服务范围"),
            ("再举个例子", "out_of_scope", "服务范围"),
            ("写个 Python 函数提取 B07SSCD71B", "out_of_scope", "服务范围"),
            ("哪些商品可以查询？", "catalog", "商品资料"),
            ("除了上面这些，还有哪些商品", "catalog", "接着上次目录"),
            ("搜索 ZXQKJ427957188", "not_found", "没有检索到"),
        ),
    ),
    (
        "site-source-absence",
        (
            ("B000EOSG8U@amazon.com 的颜色；B000EOSG8U@amazon.fr 的颜色", "answer", "Plaque 950"),
            ("B000EOSG8U@amazon.com和B000EOSG8U@amazon.fr的颜色一样吗", "answer", "不同"),
            (FAN + " 的材质是什么", "not_found", "未报告"),
            ("B0ZZZZZZZZ@amazon.com 的颜色；" + FAN + " 的宽度", "partial", "9.45 inches"),
            ("Amazon.com 的退货政策是什么", "answer", "官方快照"),
            ("Amazon.ca 的退货政策是什么", "not_found", "政策"),
            ("帮我退款", "requires_tool", "交易工具"),
            ("推荐 200 元以内的电风扇", "requires_tool", "可核实的价格"),
        ),
    ),
    (
        "compatibility",
        (
            ("给苹果8大屏找个带图案的手机壳", "clarify_model", "iphone 8 plus"),
            ("是的", "found", "iphone 8 plus"),
            ("第一款能配吗？", "compatible", "iphone 8 plus"),
            ("这款的品牌是什么", "answer", "品牌"),
            ("B07GFX9DWX 能用在 Keurig 2.0 吗？", "compatible", "keurig"),
            ("这款现在有货吗", "requires_tool", "库存"),
        ),
    ),
)


def run_validation(session, result_path, *, require=False):
    checks = []
    for scenario, cases in SCENARIOS:
        session.respond("重置")
        for question, decision, contains in cases:
            row = session.respond(question)
            text = row["answer"]
            passed = row["decision"] == decision and contains.casefold() in text.casefold()
            if decision in {"requires_tool", "out_of_scope"}:
                passed &= not row["citations"] and not row.get("retrieved_pages")
            if decision in {"answer", "compatible", "found", "partial"}:
                passed &= bool(row["citations"])
            if "完整的展示出来" in question:
                passed &= len(row.get("claims", [])) > 6 and row["current_listing_keys_after"] == [
                    FAN
                ]
            checks.append(
                {
                    "scenario": scenario,
                    "question": question,
                    "expected": decision,
                    "actual": row["decision"],
                    "required_text": contains,
                    "evidence": row["citations"],
                    "passed": passed,
                    "answer": text,
                    "error": row.get("error"),
                    "listing_keys": row.get("current_listing_keys_after", []),
                }
            )
    summary = {
        "cases": len(checks),
        "passed": sum(check["passed"] for check in checks),
        "checks": checks,
    }
    Path(result_path).write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", "utf-8")
    if require:
        assert summary["passed"] == summary["cases"], [row for row in checks if not row["passed"]]
    return summary


def run_category_validation(session, result_path):
    """Verify selection and follow-up for one actual item in every source category."""
    from catalog_labels_zh import CATEGORY_LABELS
    from full_catalogue import normalize_search, overview_items

    database = session.app.retriever
    checks = []
    for category in database.manifest["product_types"]:
        row = database.db.execute(
            "SELECT * FROM products WHERE product_type=? ORDER BY id LIMIT 1", (category,)
        ).fetchone()
        item = dict(row)
        key = item["listing_key"]
        picked = session.respond(key)
        first = session.respond("这款的属性")
        last = session.respond("把全部资料完整展示出来")
        phrase = f"我想查{CATEGORY_LABELS[category]}这类商品"
        search = normalize_search(phrase, database.manifest)
        expected = {
            field["item_id"]
            for field in overview_items(item, {"pages": []}, include_title_dimension=True)
        }
        actual = {claim["item_id"] for claim in last.get("claims", [])}
        passed = (
            picked["decision"] == "selected"
            and first["decision"] == last["decision"] == "answer"
            and last["current_listing_keys_after"] == [key]
            and bool(last["citations"])
            and expected == actual
            and all(claim["listing_key"] == key for claim in last.get("claims", []))
            and category in search["inferred_product_types"]
        )
        checks.append(
            {
                "category": category,
                "listing_key": key,
                "passed": passed,
                "expected_items": len(expected),
                "actual_items": len(actual),
            }
        )
    summary = {
        "categories": len(checks),
        "passed": sum(row["passed"] for row in checks),
        "checks": checks,
    }
    Path(result_path).write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", "utf-8")
    assert summary["passed"] == summary["categories"] == 576, [
        row for row in checks if not row["passed"]
    ][:12]
    return summary


def run_presentation_validation(session, result_path):
    from app.abo.presentation import KEY

    session.respond("重置")
    turns = (
        ("我想查询电风扇这类商品", "AmazonBasics", "第一款"),
        (FAN, "Portable Tower Fan", "继续问"),
        ("帮我看一下这个属性" + FAN, "颜色是 White", "完整展示"),
        ("还有呢给我完整的展示出来", "Portable Tower Fan", "商品原文"),
        (COFFEE + " 和 " + OTHER + " 哪款更重", "AmazonFresh", "重量"),
        (COFFEE + " 和 " + OTHER + " 哪款更贵", "无法判断", "ABO"),
        ("Amazon.com 的退货政策是什么", "官方快照", "退货"),
    )
    checks = []
    for question, first, second in turns:
        row = session.respond(question)
        answer = row["display_answer"]
        citations = row["display_sources"]
        passed = (
            first in answer
            and second in answer
            and not KEY.search(answer)
            and all(not KEY.search(source["label"]) for source in citations)
            and [source["citation"] for source in citations] == row["citations"]
        )
        if "查询电风扇" in question:
            passed &= {item["product_type"] for item in row["catalog_items"]} == {"ELECTRIC_FAN"}
        checks.append(
            {
                "question": question,
                "passed": passed,
                "display_answer": answer,
                "display_sources": citations,
                "original_answer": row["answer"],
            }
        )
    session.respond(FAN)
    row = session.respond("这款商品的编号是什么")
    checks.append(
        {
            "question": row["question"],
            "passed": FAN in row["display_answer"],
            "display_answer": row["display_answer"],
            "original_answer": row["answer"],
        }
    )
    result = {
        "cases": len(checks),
        "passed": sum(item["passed"] for item in checks),
        "checks": checks,
    }
    Path(result_path).write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", "utf-8")
    assert result["passed"] == result["cases"], [item for item in checks if not item["passed"]]
    return result
