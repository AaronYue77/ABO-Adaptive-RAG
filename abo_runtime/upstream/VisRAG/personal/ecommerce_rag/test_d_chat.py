"""Regressions for the unified terminal and transitions between ordinary and accessory requests."""
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from d_chat import StageDChat
from common import WORKSPACE, read_jsonl


class StageDChatTests(unittest.TestCase):
    def setUp(self):
        root = WORKSPACE / "reports/ecommerce_rag"
        root.mkdir(parents=True,exist_ok=True)
        folder = TemporaryDirectory(dir=root)
        self.addCleanup(folder.cleanup)
        self.out = Path(folder.name)
        assert self.out.resolve().is_relative_to(root)
        log = patch("audit.ALL_RESULTS_TXT", self.out / "aggregate.txt")
        log.start()
        self.addCleanup(log.stop)
        self.chat = StageDChat(self.out)
        self.addCleanup(self.chat.close)

    def test_reported_sequence_queries_the_real_catalog(self):
        intro = self.chat.respond("你好")
        examples = self.chat.respond("你可以查询哪些商品，举几个例子")
        search = self.chat.respond("茶杯")
        self.assertEqual([r["decision"] for r in (intro,examples,search)], ["help","catalog","search_results"])
        self.assertEqual(len(examples["catalog_examples"]),3)
        self.assertEqual({r["product_type"] for r in search["catalog_items"]},{"DRINKING_CUP"})
        self.assertEqual(search["global_catalog_records"],147702)
        self.assertIn("147,702",examples["answer"])
        self.assertEqual(len(read_jsonl(self.out/"results.jsonl")),3)

    def test_short_product_names_across_categories(self):
        for text in ("书柜","机油","洗发水","台灯","sneakers","AirPods","钢化膜","马克杯","玩具","剃须刀"):
            with self.subTest(text=text):
                value = self.chat.respond(text)
                self.assertEqual(value["decision"], "search_results")
                self.assertEqual(value["entry_lane"], "product")
                self.assertTrue(value["catalog_items"])

    def test_product_followup_and_citations_on_cpu(self):
        self.chat.respond("查询茶杯")
        picked = self.chat.respond("第一款")
        weight = self.chat.respond("第一款的材质是什么")
        self.assertEqual(picked["decision"],"selected")
        self.assertIn(weight["decision"], {"answer","not_found"})
        self.assertEqual(weight["current_listing_keys_after"], picked["current_listing_keys_after"])
        if weight["decision"]=="answer":
            self.assertTrue(weight["citations"])

    def test_catalog_pagination_and_actual_named_category(self):
        first=self.chat.respond("商品列表")
        more=self.chat.respond("还有呢")
        self.assertEqual(first["decision"],more["decision"])
        self.assertNotEqual(first["catalog_categories"],more["catalog_categories"])
        self.assertEqual(self.chat.respond("马克杯")["decision"],"search_results")

    def test_ordinary_topics_do_not_query_products_on_cpu(self):
        self.chat.respond("茶杯")
        self.chat.respond("第一款")
        for request in ("什么是库存周转率", "写一个Python正则查找型号", "讲个海边的故事", "为什么冬天水管会冻裂",
                        "找一个周末放松的方法", "搜索法国首都"):
            result = self.chat.respond(request)
            self.assertEqual(result["decision"],"out_of_scope")
            self.assertEqual(result["entry_lane"],"scope")
            self.assertFalse(result["retrieved_pages"])

    def test_small_talk_uses_b_reader_and_general_history(self):
        self.chat.app.model_available = True
        self.chat.respond("茶杯")
        self.chat.respond("第一款")
        with patch.object(self.chat.app.conversation,"reply",side_effect=["第一段","第二段"]) as model:
            first = self.chat.respond("讲个笑话")
            followup = self.chat.respond("再来一个")
        self.assertEqual((first["decision"],followup["decision"]),("chat","chat"))
        self.assertFalse(first["retrieved_pages"])
        self.assertEqual(model.call_args_list[1].args[1][0]["assistant"],"第一段")

    def test_catalog_boundary_returns_explicit_result(self):
        self.chat.respond("茶杯")
        for text in ("第一个多少钱", "帮我退款", "查订单", "这款现在有货吗"):
            with self.subTest(text=text):
                self.assertEqual(self.chat.respond(text)["decision"], "requires_tool")
        empty = self.chat.respond("搜索 ZXQKJ427957188")
        self.assertEqual(empty["decision"], "not_found")

    def test_price_comparisons_and_budgets_stop_before_retrieval(self):
        self.chat.respond("咖啡")
        self.chat.app.model_available = True
        questions = (
            "请问 B07Z6DLFQS@amazon.com和B083V9KNTS@amazon.com哪一个更贵",
            "第一款和第二款哪个更便宜",
            "这两款打折吗",
            "B07Z6DLFQS@amazon.com 价格",
            "最便宜的耳机",
            "推荐 200 元以内的电风扇",
            "Which is cheaper, B07Z6DLFQS or B083V9KNTS?",
        )
        with patch.object(self.chat.app.retriever,"get") as get, \
             patch.object(self.chat.app.retriever,"search") as search, \
             patch.object(self.chat.app.conversation,"route") as route:
            for text in questions:
                with self.subTest(text=text):
                    result = self.chat.respond(text)
                    self.assertEqual(result["decision"], "requires_tool")
                    self.assertEqual(result["routing"]["reason"], "no_verified_purchase_prices")
                    self.assertEqual(result["answer"].count("可核实的价格"), 1)
                    self.assertFalse(result["citations"])
                    self.assertFalse(result["retrieved_pages"])
            get.assert_not_called()
            search.assert_not_called()
            route.assert_not_called()

    def test_nonprice_metadata_and_policy_stay_available(self):
        metadata = self.chat.respond(
            "B07Z6DLFQS@amazon.com和B083V9KNTS@amazon.com的品牌")
        self.assertEqual(metadata["decision"], "answer")
        self.assertEqual({claim["listing_key"] for claim in metadata["claims"]},
                         {"B07Z6DLFQS@amazon.com","B083V9KNTS@amazon.com"})
        self.assertEqual(self.chat.respond("帮我写一段查询价格的 Python 代码")["decision"], "out_of_scope")
        policy = self.chat.respond("Amazon.com 的退货政策是什么")
        self.assertEqual(policy["decision"], "answer")
        self.assertTrue(policy["citations"])

    def test_child_compatibility_and_catalog_share_state_safely(self):
        first = self.chat.respond("给苹果8大屏找个有图案的手机壳")
        second = self.chat.respond("是的")
        reviewed = self.chat.respond("第一款能配吗？")
        brand = self.chat.respond("这款的品牌是什么")
        self.assertEqual((first["decision"],second["decision"],reviewed["decision"],brand["decision"]),
                         ("clarify_model","found","compatible","answer"))
        self.assertEqual(brand["current_listing_keys_after"][0],second["matches"][0]["listing"])
        self.assertTrue(brand["citations"])
        self.assertEqual(self.chat.respond("书柜")["decision"],"search_results")

    def test_direct_identified_compatibility_can_forward_a_field_question(self):
        result=self.chat.respond("B07T9TLVJ3适用 iPhone 8 Plus 吗？")
        self.assertEqual(result["decision"],"compatible")
        brand=self.chat.respond("这款的品牌是什么")
        self.assertEqual(brand["current_listing_keys_after"],["B07T9TLVJ3@amazon.in"])
        self.assertEqual(brand["decision"],"answer")

    def test_non_phone_compatibility_is_available(self):
        result=self.chat.respond("B07GFX9DWX 能用在 Keurig 2.0 吗？")
        self.assertEqual((result["entry_lane"],result["decision"]),("compatibility","compatible"))

    def test_switch_topic_does_not_apply_old_pending_confirmation(self):
        self.chat.respond("帮我找苹果8大屏的手机壳")
        self.chat.respond("茶杯")
        later=self.chat.respond("是的")
        self.assertNotEqual(later["entry_lane"],"compatibility")
        self.assertIsNone(later.get("device"))

    def test_reset_clears_both_contexts(self):
        self.chat.respond("茶杯")
        self.chat.respond("第一款")
        self.chat.respond("帮我找苹果8大屏的手机壳")
        reset=self.chat.respond("重置")
        self.assertEqual(reset["decision"],"reset")
        self.assertFalse(self.chat.catalog.selected)
        self.assertIsNone(self.chat.device_state)
        self.assertFalse(self.chat.pending)
        self.assertEqual(self.chat.respond("第一款的品牌是什么")["decision"],"clarify")


if __name__ == "__main__":
    unittest.main()
