"""Supplemental real-8B acceptance; separate from the frozen A-D evaluation."""

from uuid import uuid4


def verify(chat):
    from common import WORKSPACE, write_json

    from app.abo.cloud import cloud_request
    from app.abo.http import web_sources
    from app.abo.web_catalog import WebCatalog

    catalog = WebCatalog(WORKSPACE / "data/ecommerce_rag/abo-full-v1")
    checks = []

    def ask(identity, question):
        response = cloud_request(
            chat, {"thread_id": identity, "request_id": uuid4().hex, "question": question}
        )
        row = response["row"]
        for call in row.get("model_calls", []):
            if call.get("stage") in {"adaptive_source_answer", "adaptive_source_repair"}:
                assert call.get("adapter_mode") == "base", call
        checks.append(
            {
                "question": question,
                "question_id": row["question_id"],
                "answer": row["display_answer"],
                "citations": row["citations"],
                "task_plan": row.get("task_plan"),
                "model_executed": any(
                    call.get("model_executed") for call in row.get("model_calls", [])
                ),
            }
        )
        print(question, "\n", row["display_answer"], flush=True)
        return row

    try:
        basketball = uuid4().hex
        row = ask(basketball, "推荐几款篮球，给我看图片")
        items = row.get("catalog_items", [])
        assert len(items) >= 3, "Basketball search did not return three candidates"
        assert all(
            row["candidate_positions"][item["listing_key"]] == index
            for index, item in enumerate(items, 1)
        )
        expected = items[2]["listing_key"]
        row = ask(basketball, "给我介绍一下第三款篮球")
        assert row["synthesis_guard"]["passed"], row["synthesis_guard"]
        assert expected + ":metadata" in row["citations"], row["citations"]
        sources = web_sources(row, catalog)
        source = next(source for source in sources if source["citation"] == expected + ":metadata")
        assert source["choice"] == 3 and source.get("product_id"), source
        assert catalog.details(source["product_id"])["key"] == expected

        coat = uuid4().hex
        for question in (
            "你好，我想给我的10岁女儿买一件黑色外套，你有什么可以推荐的吗？",
            "她的身高142cm，体重33kg，要求是羊毛材质",
            "可以，请问羊毛外套有什么颜色的？",
            "什么年龄都可以，请你给我找一下有哪些羊毛外套可以选择",
        ):
            row = ask(coat, question)
            traces = row.get("retrieval_trace", {}).get("tasks", [])
            if "羊毛" in question:
                assert traces and all(
                    trace.get("required_source_criteria", {}).get("material") == "羊毛"
                    for trace in traces
                ), traces
            if "颜色" in question or "什么年龄" in question:
                assert all(
                    "color" not in trace.get("required_source_criteria", {}) for trace in traces
                ), traces
            if "什么年龄" in question:
                assert row["task_plan"]["needs"].get("age") is None
            if not row.get("requirement_checks") and not row["citations"]:
                assert "这次检索没有找到" in row["display_answer"]

        cup = uuid4().hex
        ask(cup, "找几款黑色陶瓷杯")
        row = ask(cup, "陶瓷杯有什么颜色的？")
        assert row["synthesis_guard"]["passed"], row["synthesis_guard"]
        traces = row.get("retrieval_trace", {}).get("tasks", [])
        assert traces and all(
            trace.get("required_source_criteria", {}).get("material") == "陶瓷"
            and "color" not in trace.get("required_source_criteria", {})
            for trace in traces
        ), traces
        assert all(item["model_executed"] for item in checks), checks
        assert "是无色" not in row["display_answer"] and "是透明" not in row["display_answer"], row[
            "display_answer"
        ]
        result = {
            "status": "passed",
            "turns": len(checks),
            "checks": checks,
            "scope": "Supplemental fixed dialogue regressions; not all-category quality or frozen A-D scores",
        }
        write_json(chat.output / "web-dialogue-acceptance.json", result)
        return result
    except Exception as error:
        write_json(
            chat.output / "web-dialogue-acceptance.json",
            {"status": "failed", "error": str(error), "checks": checks},
        )
        raise
