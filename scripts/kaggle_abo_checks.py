"""Run the saved GPU conversation and source-field release checks."""


def verify(session):
    import re
    from common import write_json
    from full_catalogue import catalog_page

    cases = (
        ("你好", "chat"),
        ("你可以查询哪些商品，举几个例子", "catalog"),
        ("茶杯", "search_results"),
        ("第一款", "selected"),
        ("它的材质是什么", "answer"),
        ("给苹果8大屏找个带图案的手机壳", "clarify_model"),
        ("是的", "found"),
        ("第一款能配吗？", "compatible"),
        ("帮我看图，B07T9TLVJ3能配 iPhone 8 Plus 吗？", "compatible"),
        ("请解释为什么白天有时也能看到月亮。", "out_of_scope"),
        ("再举个例子", "out_of_scope"),
        ("写一个 Python 函数，从字符串提取 B07T9TLVJ3", "out_of_scope"),
        ("讲个笑话", "chat"),
        ("书柜", "search_results"),
        ("帮我退款", "requires_tool"),
        ("你有哪些商品可以告诉我呢？", "catalog"),
        ("除了上面这些，还有哪些商品", "catalog"),
        ("再展示没有列过的类目", "catalog"),
        ("搜索宠物用品", "search_results"),
    )
    checks = []
    catalog_pages = []
    seen = set()
    manifest = session.app.retriever.manifest
    offset = 0
    while offset < len(manifest["product_types"]):
        _, offset, items = catalog_page(manifest, offset)
        for item in items:
            kind = item["product_type"]
            assert kind not in seen and re.search(r"[\u4e00-\u9fff]", item["label"]), item
            seen.add(kind)
    assert seen == set(manifest["product_types"])
    for text, expected in cases:
        result = session.respond(text)
        if expected == "catalog":
            trace = result["retrieval_trace"]
            catalog_pages.append({"start": trace.get("category_offset"), "end": trace.get("next_category_offset"),
                                  "items": result.get("catalog_categories", [])})
        calls = result.get("model_calls", [])
        checks.append({"question": text, "expected": expected, "actual": result["decision"],
                       "lane": result["entry_lane"], "answer": result["answer"],
                       "model_executed": any(x.get("model_executed") for x in calls),
                       "has_evidence": bool(result.get("citations") or result.get("retrieved_pages")),
                       "visual": bool(result.get("visual_observation")),
                       "errors":result.get("errors",[])})
    adaptive = verify_adaptive(session)
    summary = {"checks": checks, "adaptive_checks": adaptive, "model_loads": session.app.reader.load_count,
               "bilingual_categories": len(seen), "catalog_pages": catalog_pages}
    write_json(session.output / "gpu-summary.json", summary)
    for check in checks:
        print(check["expected"], check["actual"], check["lane"], check["question"], flush=True)
    assert all(row["actual"] == row["expected"] for row in checks), checks
    assert all(row["model_executed"] for row in checks if row["expected"] == "chat"), checks
    assert all(not row["model_executed"] and not row["has_evidence"]
               for row in checks if row["expected"] == "out_of_scope"), checks
    assert checks[8]["visual"] and summary["model_loads"] == 1
    recent_pages = catalog_pages[-3:]
    assert [(page["start"], page["end"]) for page in recent_pages] == [(0, 14), (14, 28), (28, 42)]
    assert len({item["product_type"] for page in recent_pages for item in page["items"]}) == 42
    assert "宠物用品（PET_SUPPLIES）" in checks[-3]["answer"]
    assert result["catalog_items"] and all(item["product_type"] == "PET_SUPPLIES" for item in result["catalog_items"])
    print(f"Bilingual catalog: {len(seen)} source categories; first 3 pages: 42 unique categories", flush=True)
    return summary


def verify_adaptive(session):
    cases = (
        ("给我这个“B08BFW6627”的属性，谢谢", "answer", ("UMI", "Baby Blue", "4'x3'")),
        ("B000EOSG8U@amazon.com 的颜色；B000EOSG8U@amazon.fr 的颜色", "answer", ("950 PLAT", "Plaque 950")),
        ("B0ZZZZZZZZ@amazon.com 的颜色；B076QBF37R@amazon.in 的高度", "partial", ("未找到", "480 millimeters")),
        ("请先查一下 B079J12C4W@amazon.com/go 的品牌，再查一下 B07KB1RTFF@amazon.sg 的品牌",
         "answer", ("Amazon Basic Care", "AmazonBasics")),
    )
    checks = []
    for question, expected, values in cases:
        row = session.respond(question)
        passed = (row["decision"] == expected and bool(row["citations"]) and not row["model_calls"]
                  and all(value in row["answer"] for value in values))
        checks.append({"question": question, "decision": row["decision"], "passed": passed,
                       "citations": row["citations"], "elapsed_seconds": row["total_seconds"]})
        print("Adaptive", row["decision"], passed, question, flush=True)
    assert all(row["passed"] for row in checks), checks
    return checks


def verify_categories(session):
    from a_category_audit import DATA, freeze, run

    if not DATA.exists():
        freeze()
    result = run(session.output / "category-coverage")
    assert result["passing_categories"] == result["categories"] == 576
    print(f"Source-field audit: {result['passed']}/{result['cases']} across {result['categories']} categories", flush=True)
    return result
