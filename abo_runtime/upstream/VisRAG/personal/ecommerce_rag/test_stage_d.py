"""Compatibility identity and cross-agent contract regressions on the real catalog."""
import unittest

from full_retrieval import FullRetriever
from c8_reader import ReaderAdapter
from d_compat_catalog import Evidence, classify, resolve_models
from d_compat_route import DemandClassifier, train_router
from stage_d import StageD


def quote(text):
    return Evidence("source","key","title",text,"metadata",1,"hash")


class StageDTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            cls.catalog = FullRetriever()
        except FileNotFoundError as error:
            raise unittest.SkipTest(str(error))
        cls.addClassCleanup(cls.catalog.close)

    def setUp(self):
        self.app = StageD(self.catalog)

    def test_casual_variant_then_clarify_and_evidence(self):
        first = self.app.respond("给苹果8大屏找个带图案的手机壳",site="amazon.in")
        self.assertEqual(first["decision"],"clarify_model")
        second = self.app.respond("是的",first["state"],site="amazon.in")
        self.assertEqual(second["decision"],"found")
        self.assertTrue(second["citations"])
        third = self.app.respond("第一款能配吗？",second["state"])
        self.assertEqual(third["decision"],"compatible")
        self.assertEqual(third["request"]["domain"],"amazon.in")
        self.assertEqual(third["request"]["model"],"iphone 8 plus")

    def test_independent_consumable_and_console_sources(self):
        for stem, site, named in (
            ("B07GFX9DWX能给Keurig 2.0用吗？","amazon.com","keurig 2.0"),
            ("B073PVWNDD兼容PS4吗？","amazon.sg","playstation 4"),
            ("B0757TMST4 适配惠普56吗？","amazon.com.au","hp 56"),
        ):
            with self.subTest(stem=stem):
                row = self.app.respond(stem,site=site)
                self.assertEqual(row["decision"],"compatible")
                self.assertEqual(row["device"]["model"],named)
                self.assertTrue(row["citations"])

    def test_real_explicit_negative(self):
        row = self.app.respond("B07VBJ1YL6能配 Huawei P20 Lite 2019 吗？",site="amazon.co.uk")
        self.assertEqual(row["decision"],"incompatible")
        self.assertTrue(any("NOT For 2019 Version" in e["quote"] for e in row["evidence"]))

    def test_one_role_recovers_temporary_tool_failure(self):
        value = self.app.respond("B07T9TLVJ3适用 iPhone 8 Plus 吗？",site="amazon.in",
                                 inject={"agent":"retrieve","persistent":False})
        steps = [x for x in value["trace"] if x["agent"]=="retrieve"]
        self.assertEqual([x["status"] for x in steps],["error","ok"])
        self.assertEqual(value["decision"],"compatible")

    def test_fabricated_verdict_fails_closed(self):
        value = self.app.respond("B07T9TLVJ3适用 iPhone 7 吗？",site="amazon.in",
                                 inject={"agent":"review","persistent":True,"kind":"invalid_verdict"})
        self.assertEqual(value["decision"],"unavailable")
        self.assertEqual(value["failure_stage"],"review")
        self.assertFalse(value["citations"])

    def test_single_and_multi_cannot_infer_mismatch(self):
        for mode in ("single","multi"):
            value = StageD(self.catalog,mode=mode).respond("B07T9TLVJ3适用 iPhone 7 吗？",site="amazon.in")
            self.assertEqual(value["decision"],"unknown")

    def test_model_variant_tokens_are_whole(self):
        self.assertEqual(resolve_models("给苹果8大屏换壳"),(["iphone 8 plus"],"informal_variant"))
        self.assertEqual(classify(quote("Case for iPhone 11 Pro"),"iphone 11"),set())
        self.assertEqual(classify(quote("Case for iPhone 8 Plus"),"iphone 8"),set())
        self.assertEqual(classify(quote("Case for Galaxy S21 Ultra"),"galaxy s21"),set())

    def test_conflicting_polarity_and_generic_catalogue_codes(self):
        q=quote("Phone case compatible with iPhone 8, not compatible with iPhone 8.")
        self.assertEqual(classify(q,"iphone 8"),{"compatible","incompatible"})
        self.assertEqual(classify(quote("Replacement Filter compatible with ACX-501"),"acx-501"),{"compatible"})
        self.assertEqual(classify(quote("Replacement Filter compatible with ACX-501a"),"acx-501"),set())

    def test_catalog_scope_is_independent_of_queries(self):
        self.assertEqual((self.catalog.manifest["records"],len(self.catalog.manifest["product_types"]),
                          len(self.catalog.manifest["domains"])),(147702,576,32))

    def test_classifier_is_trained_and_shared(self):
        agent=DemandClassifier(train_router([
            {"text":"我想找个新手机壳","intent":"find"},
            {"text":"这款保护壳适配哪个型号","intent":"check"},
            {"text":"天气怎么样了","intent":"other"}]))
        self.assertEqual(agent.predict("天气怎么样了")[0],"other")

    def test_active_c_adapter_fingerprint(self):
        adapter = ReaderAdapter.accepted()
        self.assertEqual(len(adapter.sha256),64)

    def test_new_model_clears_stale_accessory_on_search(self):
        first = self.app.respond("B07T9TLVJ3适用 iPhone 8 Plus 吗？",site="amazon.in")
        second = self.app.respond("帮我找适合 Pixel 3a 的保护壳",first["state"],site="amazon.in")
        self.assertEqual(second["request"]["listing_sku"],"")
        self.assertEqual(second["request"]["model"],"pixel 3a")


if __name__=="__main__":
    unittest.main()
