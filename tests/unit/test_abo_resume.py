"""Conversation recovery, idempotent retries and source-backed audience checks."""

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient

from app.abo.service import AboDialogue, bind_runtime

bind_runtime()


class ResumeTests(unittest.TestCase):
    def setUp(self):
        import audit
        import common

        self.folder = TemporaryDirectory(dir=common.WORKSPACE / "cache/tmp")
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        for module, key in ((common, "OPERATIONS"), (common, "ISSUES"), (audit, "ALL_RESULTS_TXT")):
            guard = patch.object(module, key, self.root / (key + ".txt"))
            guard.start()
            self.addCleanup(guard.stop)

    def test_two_threads_survive_restart_without_resetting_each_other(self):
        from app.abo.http import create_abo_app
        from app.abo.state import ThreadStore

        store = ThreadStore(self.root / "threads")
        with patch("app.abo.http.ThreadStore", return_value=store):
            first, second = uuid4().hex, uuid4().hex
            with TestClient(create_abo_app()) as client:
                client.post(
                    "/api/v1/chat",
                    json={"question": "B07SSCD71B@amazon.com.au 的品牌是什么", "thread_id": first},
                ).raise_for_status()
                client.post(
                    "/api/v1/chat", json={"question": "搜索红色高跟鞋", "thread_id": second}
                )
                request_id = uuid4().hex
                payload = {
                    "question": "这款的型号是什么",
                    "thread_id": first,
                    "request_id": request_id,
                }
                response = client.post("/api/v1/chat", json=payload)
                self.assertEqual(response.status_code, 200, response.text)
                self.assertIn("FZ10-17KB UK", response.json()["answer"])
                self.assertEqual(client.post("/api/v1/chat", json=payload).json(), response.json())
                self.assertEqual(len(store.get(first)["messages"]), 4)
            with TestClient(create_abo_app()) as client:
                result = client.post(
                    "/api/v1/chat", json={"question": "这款的颜色是什么", "thread_id": first}
                )
                self.assertIn("White", result.json()["answer"])
                result = client.post(
                    "/api/v1/chat", json={"question": "第三款", "thread_id": second}
                )
                self.assertEqual(result.json()["action"], "selected")
                self.assertEqual(len(client.get("/api/v1/threads").json()["items"]), 2)

    def test_cloud_threads_and_retries_share_model_without_sharing_product_selection(self):
        from app.abo.cloud import cloud_request
        from app.abo.state import ThreadStore

        store = ThreadStore(self.root / "cloud")
        chat = AboDialogue(self.root / "chat")
        self.addCleanup(chat.close)
        first, second = uuid4().hex, uuid4().hex
        with patch("app.abo.cloud.ThreadStore", return_value=store):
            payload = {
                "thread_id": first,
                "request_id": uuid4().hex,
                "question": "B07SSCD71B@amazon.com.au 的品牌是什么",
            }
            row = cloud_request(chat, payload)
            turn = chat.engine.turn
            self.assertEqual(cloud_request(chat, payload), row)
            self.assertEqual(chat.engine.turn, turn)
            cloud_request(
                chat, {"thread_id": second, "request_id": uuid4().hex, "question": "搜索红色高跟鞋"}
            )
            reply = cloud_request(
                chat, {"thread_id": first, "request_id": uuid4().hex, "question": "这款型号是什么"}
            )
            self.assertIn("FZ10-17KB UK", reply["row"]["display_answer"])
            reply = cloud_request(
                chat, {"thread_id": second, "request_id": uuid4().hex, "question": "第三款"}
            )
            self.assertEqual(reply["row"]["decision"], "selected")

    def test_transient_transport_failure_retries_same_request_and_preserves_dialogue(self):
        from unittest.mock import Mock

        from app.abo.state import export_state
        from app.abo.web_connection import ChatConnectionError, RemoteDialogue

        chat = AboDialogue(self.root / "real")
        try:
            row = chat.respond("手表类商品有多少个？")
            config = json.loads((chat.output / "config.json").read_text("utf-8"))
            state = export_state(chat)
        finally:
            chat.close()
        startup, failed, recovered, next_turn = Mock(), Mock(), Mock(), Mock()
        startup.execute.return_value = config
        failed.execute.side_effect = ChatConnectionError("Kaggle 会话已断开。")
        recovered.execute.return_value = next_turn.execute.return_value = {
            "row": row,
            "state": state,
        }
        with patch(
            "app.abo.web_connection.connect", side_effect=[startup, failed, recovered, next_turn]
        ):
            remote = RemoteDialogue(
                self.root / "remote", "https://example.kaggle.net/lab?token=private-token"
            )
            reply = remote.respond(
                "手表类商品有多少个？", thread_id=uuid4().hex, request_id=uuid4().hex
            )
            self.assertEqual(reply, row)
            self.assertEqual(failed.execute.call_args, recovered.execute.call_args)
            self.assertFalse(remote.journal.closed)
            remote.respond("继续")
            remote.close()
        for path in (self.root / "remote").glob("*.txt"):
            self.assertNotIn("private-token", path.read_text("utf-8"))

    def test_old_notebook_is_identified_before_the_first_question(self):
        from unittest.mock import Mock

        from app.abo.web_connection import ChatConnectionError, RemoteDialogue

        kernel = Mock()
        kernel.execute.return_value = {"entry": "old-version"}
        with patch("app.abo.web_connection.connect", return_value=kernel):
            with self.assertRaisesRegex(ChatConnectionError, "旧版"):
                RemoteDialogue(
                    self.root / "old-kaggle", "https://test.kaggle.net/lab?token=private-token"
                )
        kernel.close.assert_called()
        text = (self.root / "old-kaggle/运行记录.txt").read_text("utf-8")
        self.assertNotIn("private-token", text)

    def test_age_intervals_are_evidence_and_gender_specific(self):
        from app.abo.turns import age_check

        def source(text):
            return {"raw_json": json.dumps({"bullet_point": [{"value": text}]})}

        text = source("ideal for female players ages 9 and older and male players ages 9 to 11")
        self.assertEqual(age_check(text, {"age": 12, "gender": "boy"}), "conflict")
        self.assertEqual(age_check(text, {"age": 12, "gender": "girl"}), "supported")
        self.assertEqual(age_check(text, {"age": 8, "gender": "boy"}), "conflict")
        self.assertEqual(age_check(source("For routine practice"), {"age": 8}), "unknown")

    def test_explicit_red_value_is_not_overruled_by_normalized_black(self):
        from full_retrieval import FullRetriever

        retriever = FullRetriever()
        self.addCleanup(retriever.close)
        source = retriever.get("B076Q8MTX6", "amazon.in")[0]
        raw = json.loads(source["raw_json"])
        self.assertEqual(raw["color"][0]["value"], "Red")
        self.assertEqual(raw["color"][0]["standardized_values"], ["Black"])
        black = retriever.search(
            "backpack", "bm25", 50, {"product_type": "BACKPACK"}, criteria={"color": "黑色"}
        )
        self.assertNotIn(source["listing_key"], [item["listing_key"] for item in black["items"]])
        for item in black["items"]:
            raw = json.loads(item["raw_json"])
            original = (
                " ".join(entry.get("value", "") for entry in raw.get("color", [])) or item["title"]
            )
            self.assertTrue(
                "black" in original.casefold() or "黑色" in original, item["listing_key"]
            )

    def test_compatibility_after_search_does_not_cite_prior_candidates(self):
        chat = AboDialogue(self.root / "change-explicit-product")
        self.addCleanup(chat.close)
        chat.respond("有哪些电风扇，给我看看图片")
        stale = set(chat.engine.catalog.candidates)
        self.assertTrue(stale)
        chat.app.model_available = True
        plan = {
            "tasks": [
                {"tool": "compatibility", "question": "B07T9TLVJ3能配 iPhone 8 Plus 吗？"},
                {"tool": "lookup", "question": "查看这款的兼容描述"},
            ],
            "query": "phone case",
            "needs": {},
            "focus": [],
            "images": False,
        }
        with patch.object(
            chat.app.conversation, "generate", return_value=json.dumps(plan, ensure_ascii=False)
        ):
            row = chat.respond("B07T9TLVJ3能配 iPhone 8 Plus 吗？")
        self.assertEqual(row["decision"], "compatible")
        self.assertTrue(row["citations"])
        self.assertTrue(
            all(not any(key in citation for key in stale) for citation in row["citations"])
        )
        self.assertNotIn("ELECTRIC_FAN", json.dumps(row.get("requirement_checks", [])))

    def test_changed_requirements_trigger_new_evidence_even_if_planner_only_says_advice(self):
        from app.abo.turns import complete_plan

        for text in ("改成蓝色，其他用途保持", "排除黑色，再说明理由", "只看39码的"):
            with self.subTest(text=text):
                plan = {
                    "tasks": [{"tool": "advice", "question": text}],
                    "query": "backpack",
                    "needs": {},
                    "focus": [],
                    "images": False,
                }
                updated = complete_plan(plan, text, True)
                self.assertEqual(updated["tasks"][0]["tool"], "search")
                self.assertEqual(updated["tasks"][0]["question"], text)

    def test_grounding_keeps_measurement_units_and_normalized_units(self):
        from full_retrieval import FullRetriever

        from app.abo.turns import source_item

        retriever = FullRetriever()
        self.addCleanup(retriever.close)
        product = retriever.get("B073QVZHL1", "amazon.in")[0]
        fact = source_item(product, {}, 1, "商品重量是多少")
        weight = " ".join(fact["fields"]["item_weight"])
        self.assertIn("kilograms", weight)
        self.assertIn("pounds", weight)
        self.assertIn("0.62", weight)

    def test_compound_turn_keeps_count_search_evidence_and_auditable_sources(self):
        from types import SimpleNamespace

        chat = AboDialogue(self.root / "compound")
        self.addCleanup(chat.close)
        chat.app.model_available = True

        def generate(messages, stage, trace, max_tokens, **kwargs):
            trace.append({"stage": stage, "status": "ok", "model_executed": False})
            if stage == "adaptive_turn_plan":
                return json.dumps(
                    {
                        "tasks": [
                            {"tool": "count", "question": "手表类商品有多少个？"},
                            {"tool": "search", "question": "找几款电风扇"},
                        ],
                        "query": "watch",
                        "needs": {"age": None},
                        "focus": [],
                        "images": True,
                    },
                    ensure_ascii=False,
                )
            if stage == "adaptive_evidence_review":
                return '{"supported":true,"problems":[]}'
            context = json.loads(messages[-1]["content"])
            return json.dumps(
                {
                    "answer": "手表共有28条历史记录。这里有AmazonBasics电风扇，具体用途仍需要核对。",
                    "citations": ["E1"],
                    "recommended": [context["fresh_sources"][0]["listing_key"]],
                },
                ensure_ascii=False,
            )

        reader = SimpleNamespace(
            vlm=SimpleNamespace(
                processor=SimpleNamespace(tokenizer=SimpleNamespace(encode=lambda text: []))
            )
        )
        with (
            patch.object(chat.app.conversation, "generate", side_effect=generate),
            patch.object(chat.app.reader, "ensure_reader", return_value=reader),
        ):
            result = chat.respond("手表有多少个？再找几款电风扇")
        self.assertIn("28", result["display_answer"])
        self.assertIn("abo:catalog:count", result["citations"])
        self.assertEqual(len(result["task_plan"]["tasks"]), 2)
        self.assertTrue(result["catalog_items"])
        self.assertTrue(
            all({"rank", "score", "text_source"} <= set(page) for page in result["retrieved_pages"])
        )
        self.assertIn("本轮多任务计划", (chat.output / "逐题结果.txt").read_text("utf-8"))


if __name__ == "__main__":
    unittest.main()
