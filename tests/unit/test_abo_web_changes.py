"""Kaggle-only web conversations, deletion and stable product evidence identities."""

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient

from app.abo.service import AboDialogue, bind_runtime

WORKSPACE = bind_runtime()


class WebChangesTests(unittest.TestCase):
    def setUp(self):
        import audit
        import common

        self.folder = TemporaryDirectory(dir=WORKSPACE / "cache/tmp")
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        for module, key in ((common, "OPERATIONS"), (common, "ISSUES"), (audit, "ALL_RESULTS_TXT")):
            guard = patch.object(module, key, self.root / (key + ".txt"))
            guard.start()
            self.addCleanup(guard.stop)

    def test_web_rejects_implicit_or_explicit_cpu_answers(self):
        from app.abo.http import create_abo_app

        with TestClient(create_abo_app()) as client:
            for mode in ({}, {"mode": "local"}, {"mode": "kaggle"}):
                response = client.post("/api/v1/chat", json={"question": "你好", **mode})
                self.assertEqual(response.status_code, 502)
                self.assertIn("Kaggle", response.json()["detail"])

    def test_delete_cannot_be_undone_by_refresh_stale_history_or_idempotent_retry(self):
        from app.abo.http import create_abo_app
        from app.abo.state import ThreadStore

        store = ThreadStore(self.root / "threads")
        first, other = uuid4().hex, uuid4().hex
        for identity in (first, other):
            store.save(
                identity,
                {
                    "id": identity,
                    "messages": [{"role": "user", "text": "示例"}],
                    "state": {"private": "context"},
                    "request_id": identity,
                    "reply": {"answer": "cached"},
                },
            )
        with patch("app.abo.http.ThreadStore", return_value=store):
            for _ in range(2):
                with TestClient(create_abo_app()) as client:
                    self.assertEqual(client.delete("/api/v1/threads/" + first).status_code, 204)
                    history = client.get("/api/v1/threads").json()
                    self.assertEqual([row["id"] for row in history["items"]], [other])
                    self.assertIn(first, history["deleted"])
                    response = client.post(
                        "/api/v1/chat",
                        json={
                            "question": "继续",
                            "thread_id": first,
                            "request_id": first,
                            "history": [{"role": "user", "text": "示例"}],
                        },
                    )
                    self.assertEqual(response.status_code, 410)
            self.assertNotIn("messages", store.get(first))
            self.assertNotIn("state", store.get(first))

    def test_metadata_source_names_product_and_opens_its_properties(self):
        from app.abo.http import web_sources
        from app.abo.web_catalog import WebCatalog

        catalog = WebCatalog(WORKSPACE / "data/ecommerce_rag/abo-full-v1")
        key = "B082L9GWG4@amazon.in"
        row = {
            "display_sources": [{"citation": key + ":metadata", "label": "amazon.in 商品资料"}],
            "candidate_positions": {key: 3},
        }
        source = web_sources(row, catalog)[0]
        self.assertEqual(source["choice"], 3)
        self.assertIn("Camouflage", source["label"])
        self.assertEqual(catalog.details(source["product_id"])["key"], key)
        self.assertNotIn('"id": "E1"', source["excerpt"])

    def test_imageless_products_keep_their_place_in_candidate_order(self):
        from app.abo.web_catalog import WebCatalog

        catalog = WebCatalog(WORKSPACE / "data/ecommerce_rag/abo-full-v1")
        with catalog.connect() as db:
            hairband = db.execute(
                "SELECT listing_key FROM products WHERE product_type='HAIRBAND' LIMIT 1"
            ).fetchone()[0]
        cards = catalog.chat_cards(
            {
                "decision": "search_results",
                "catalog_items": [
                    {"listing_key": hairband},
                    {"listing_key": "B082L9GWG4@amazon.in"},
                ],
            }
        )
        self.assertEqual([c["choice"] for c in cards], [1, 2])
        self.assertIsNone(cards[0]["image"])

    def test_relaxation_is_per_dimension_and_not_an_age_guarantee(self):
        from app.abo.turns import released_requirements

        for question, expected in (
            ("什么年龄都可以，请找羊毛外套", {"age"}),
            ("年龄不限，其他要求保持", {"age"}),
            ("羊毛外套有什么颜色的？", {"color"}),
            ("颜色不限，只要陶瓷的杯子", {"color"}),
            ("性别不限，尺码有哪些？", {"gender", "size"}),
            ("颜色有红色吗？", set()),
        ):
            self.assertEqual(released_requirements(question), expected, question)

    def test_material_constraints_work_for_other_product_types_and_negation(self):
        from catalog_constraints import matches_criteria, named_constraints

        for kind, material in [("HOME", "羊毛"), ("ACCESSORY", "皮革"), ("SHOES", "尼龙")]:
            criteria = named_constraints("只要" + material + "的")
            self.assertIn("material", criteria)
            raw = {
                "material": [
                    {"value": {"羊毛": "Wool", "皮革": "Leather", "尼龙": "Nylon"}[material]}
                ]
            }
            self.assertTrue(
                matches_criteria("product", kind, json.dumps(raw), json.dumps(criteria))
            )
            self.assertFalse(
                matches_criteria(
                    "product",
                    kind,
                    json.dumps(raw),
                    json.dumps(named_constraints("不要" + material)),
                )
            )

    def test_released_filter_triggers_search_even_when_planner_only_says_advice(self):
        from app.abo.turns import complete_plan

        for question in ("颜色不限，其他保持", "年龄不限，继续找", "陶瓷杯有哪些颜色？"):
            plan = {
                "tasks": [{"tool": "advice", "question": question}],
                "query": "mug",
                "needs": {},
                "focus": [],
            }
            self.assertEqual(complete_plan(plan, question, True)["tasks"][0]["tool"], "search")
        for question in ("第三款有哪些颜色？", "这款尺码有哪些？"):
            plan = {
                "tasks": [{"tool": "lookup", "question": question}],
                "query": "mug",
                "needs": {},
                "focus": [3],
            }
            self.assertEqual(complete_plan(plan, question, True)["tasks"][0]["tool"], "lookup")

    def test_third_product_overrides_incorrect_planner_focus_for_different_categories(self):
        from full_app import public_item

        for query in ("basketball", "coffee"):
            with self.subTest(query=query):
                chat = AboDialogue(self.root / query)
                self.addCleanup(chat.close)
                products = chat.app.retriever.search(query, "bm25", 4)["items"]
                chat.engine.catalog.candidates = [p["listing_key"] for p in products]
                chat.engine.catalog.candidate_entities = [public_item(p) for p in products]
                chat.engine.last_lane = "product"
                chat.app.model_available = True

                def generate(messages, stage, trace, max_tokens, query=query, products=products):
                    if stage == "adaptive_turn_plan":
                        return json.dumps(
                            {
                                "tasks": [{"tool": "advice", "question": "介绍第三款"}],
                                "query": query,
                                "needs": {},
                                "focus": [1],
                            }
                        )
                    if stage == "adaptive_evidence_review":
                        return '{"supported":true}'
                    context = json.loads(messages[-1]["content"])
                    self.assertEqual(
                        context["fresh_sources"][0]["listing_key"], products[2]["listing_key"]
                    )
                    self.assertEqual(context["fresh_sources"][0]["candidate_number"], 3)
                    return json.dumps(
                        {
                            "answer": "E1的名称是" + products[2]["title"],
                            "citations": ["E1"],
                            "recommended": [],
                        }
                    )

                reader = SimpleNamespace(
                    vlm=SimpleNamespace(
                        processor=SimpleNamespace(tokenizer=SimpleNamespace(encode=lambda _: []))
                    )
                )
                with (
                    patch.object(chat.app.conversation, "generate", side_effect=generate),
                    patch.object(chat.app.reader, "ensure_reader", return_value=reader),
                ):
                    row = chat.respond("给我介绍第三款商品")
                self.assertEqual(row["citations"], [products[2]["listing_key"] + ":metadata"])
                self.assertIn("第3款", row["display_answer"])
                self.assertEqual(row["candidate_positions"][products[2]["listing_key"]], 3)

    def test_empty_search_does_not_let_model_invent_material_or_color_claims(self):
        chat = AboDialogue(self.root / "no-evidence")
        self.addCleanup(chat.close)
        chat.app.model_available = True
        stages = []

        def generate(messages, stage, trace, max_tokens):
            stages.append(stage)
            if stage == "adaptive_turn_plan":
                return json.dumps(
                    {
                        "tasks": [{"tool": "search", "question": "搜索不存在的商品"}],
                        "query": "zzzzunknownitem",
                        "needs": {},
                        "focus": [],
                    }
                )
            self.fail("No product synthesis should run without evidence: " + stage)

        reader = SimpleNamespace(
            vlm=SimpleNamespace(
                processor=SimpleNamespace(tokenizer=SimpleNamespace(encode=lambda _: []))
            )
        )
        with (
            patch.object(chat.app.conversation, "generate", side_effect=generate),
            patch.object(chat.app.reader, "ensure_reader", return_value=reader),
        ):
            row = chat.respond("搜索zzzzunknownitem")
        self.assertEqual(row["citations"], [])
        self.assertIn("这次检索", row["display_answer"])
        self.assertEqual(stages, ["adaptive_turn_plan"])
        self.assertTrue(row["synthesis_guard"]["passed"])

    def test_relaxation_preserves_other_filters_and_measured_needs_despite_stale_plan(self):
        chat = AboDialogue(self.root / "relaxation")
        self.addCleanup(chat.close)
        chat.app.model_available = True
        chat.turn_memory = {
            "query": "mug",
            "needs": {"age": 10, "constraints": ["黑色", "陶瓷", "身高142cm"]},
            "search_contexts": [{"criteria": {"color": "黑色", "material": "陶瓷"}, "filters": {}}],
        }
        reader = SimpleNamespace(
            vlm=SimpleNamespace(
                processor=SimpleNamespace(tokenizer=SimpleNamespace(encode=lambda _: []))
            )
        )

        def generate(messages, stage, trace, max_tokens):
            if stage == "adaptive_turn_plan":
                return json.dumps(
                    {
                        "tasks": [{"tool": "search", "question": "搜索黑色杯子"}],
                        "query": "mug",
                        "needs": {"age": 10, "constraints": ["黑色"]},
                        "focus": [],
                    }
                )
            return '{"query":"mug"}'

        def search(query, mode, limit, filters, **kwargs):
            self.assertEqual(kwargs["criteria"], {"material": "陶瓷"})
            return {
                "items": [],
                "seconds": 0,
                "trace": {"required_source_criteria": kwargs["criteria"]},
            }

        with (
            patch.object(chat.app.conversation, "generate", side_effect=generate),
            patch.object(chat.app.retriever, "search", side_effect=search),
            patch.object(chat.app.reader, "ensure_reader", return_value=reader),
        ):
            row = chat.respond("不是给10岁用的了，年龄不限；颜色有哪些？体重33kg")
        needs = row["task_plan"]["needs"]
        self.assertIsNone(needs["age"])
        self.assertIn("陶瓷", needs["constraints"])
        self.assertIn("身高142cm", needs["constraints"])
        self.assertIn("体重33kg", needs["constraints"])
        self.assertNotIn("黑色", needs["constraints"])
