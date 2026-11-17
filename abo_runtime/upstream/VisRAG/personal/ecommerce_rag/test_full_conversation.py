"""Verify scope routing without running the evidence reader or the GPU model."""
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

from common import WORKSPACE, read_jsonl
from full_app import FullApplication
from full_conversation import FullConversation, clear_route, direct_business_request


class ConversationTests(unittest.TestCase):
    def setUp(self):
        folder = TemporaryDirectory(dir=WORKSPACE / "cache/tmp")
        self.addCleanup(folder.cleanup)
        self.app = FullApplication("B", report_root=Path(folder.name), audit=False)
        self.addCleanup(self.app.close)
        self.session = self.app.session()
        self.app.conversation = Mock()
        self.app.conversation.reply.return_value = "你好呀！"
        self.app.conversation.route.return_value = {"intent": "out_of_scope", "reason": "scope_spy"}

    def test_small_talk_never_searches(self):
        for text in ("你好", "你是谁", "你有什么功能？", "谢谢你", "你今天过得怎么样？", "讲个笑话", "再来一个"):
            with self.subTest(text=text), patch.object(self.app.retriever, "search") as search:
                row = self.session.respond(text)
                self.assertEqual(row["action"], "chat")
                self.assertFalse(row["citations"])
                self.assertFalse(row["retrieved_pages"])
                search.assert_not_called()
        self.app.conversation.route.assert_not_called()

    def test_unrelated_tasks_do_not_lookup_or_generate_even_with_product_context(self):
        self.session.respond("B07C5PRQHN@amazon.ca")
        with patch.object(self.app.retriever, "search") as search, patch.object(self.app.retriever, "get") as get:
            for text in ("解释一下 Python 字典和列表的区别", "写一个Python函数，从字符串提取示例编号B07C5PRQHN",
                         "写一段这款产品的营销文案", "帮我写订单管理代码", "法国的首都在哪里？",
                         "请解释为什么白天能看到月亮", "什么是库存周转率？", "换个话题，帮我写作文", "再举个例子"):
                with self.subTest(text=text):
                    result = self.session.respond(text, {"skus": ["B07C5PRQHN"], "domain": "amazon.ca"})
                    self.assertEqual(result["action"], "out_of_scope")
                    self.assertFalse(result["citations"])
                    self.assertFalse(result["retrieved_pages"])
                    self.assertEqual(self.session.selected, ["B07C5PRQHN@amazon.ca"])
            search.assert_not_called()
            get.assert_not_called()
        self.app.conversation.reply.assert_not_called()

    def test_model_classification_cannot_enable_unrestricted_chat(self):
        conversation = FullConversation(Mock())
        for raw in ('{"intent":"general"}', "unparseable", '{"intent":"out_of_scope"}'):
            conversation.generate = Mock(return_value=raw)
            result = conversation.route("我刚想起另一件事情", [], "product", [], [])
            self.assertEqual(result["intent"], "out_of_scope")

    def test_catalog_search_selection_and_product_facts(self):
        self.assertEqual(self.session.respond("搜索 headphones")["action"], "search_results")
        self.assertEqual(self.session.respond("第一个")["action"], "selected")
        self.assertTrue(self.session.selected)
        self.assertEqual(self.session.respond("B000000000 的品牌是什么")["action"], "not_found")
        row = self.session.respond("B08BFW6627@amazon.co.uk 的颜色是什么？")
        self.assertEqual(row["action"], "answer")
        self.assertIn("Baby Blue", row["answer"])
        self.assertTrue(row["citations"])
        self.app.conversation.reply.assert_not_called()

    def test_missing_product_context_is_a_product_clarification(self):
        self.assertEqual(self.session.respond("这款有多重？")["action"], "clarify")
        self.app.conversation.reply.assert_not_called()

    def test_commerce_topics_do_not_automatically_trigger_tools(self):
        for question in ("什么是库存？", "帮我写订单管理代码", "解释退款的定义"):
            self.assertFalse(direct_business_request(question))
            self.assertEqual(self.session.respond(question)["action"], "out_of_scope")
        self.assertEqual(self.session.respond("帮我办理退款")["action"], "requires_tool")
        self.assertEqual(self.session.respond("今天还有库存吗")["action"], "requires_tool")
        policy = self.session.respond("Amazon.com 的退货政策是什么？")
        self.assertEqual(policy["action"], "answer")
        self.assertTrue(policy["citations"])

    def test_small_talk_history_is_separate_bounded_and_reset(self):
        for _ in range(10):
            self.session.respond("你好")
        self.assertEqual(len(self.session.general_history), 6)
        before = list(self.session.general_history)
        self.session.respond("B07C5PRQHN@amazon.ca")
        self.session.respond("帮我写一篇小说")
        self.assertEqual(self.session.general_history, before)
        self.assertEqual(read_jsonl(self.session.out / "results.jsonl")[-1]["conversation_lane"], "scope")
        self.session.respond("重置")
        self.assertFalse(self.session.general_history)
        self.assertIsNone(self.session.last_lane)

    def test_rules_recognize_product_questions_after_scope_response(self):
        self.assertEqual(clear_route("什么是品牌", True, "product")["intent"], "out_of_scope")
        self.assertEqual(clear_route("这款品牌是什么", True, "scope")["intent"], "fact")
        self.assertEqual(clear_route("品牌是什么", True, "scope")["intent"], "fact")
        self.assertIsNone(clear_route("我刚想起一件事", False, None))

    def test_unrecognized_topic_is_bounded_in_cpu_mode(self):
        self.app.model_available = False
        self.assertEqual(self.session.respond("我想练习和陌生人交流")["action"], "out_of_scope")
        self.assertEqual(self.session.respond("陪我聊两句")["action"], "chat")
        self.app.conversation.route.assert_not_called()
        self.app.conversation.reply.assert_not_called()


if __name__ == "__main__":
    unittest.main()
