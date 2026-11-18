"""ABO logging regressions over the real catalogue, with no GPU or network."""

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from app.abo.service import AboDialogue, bind_runtime


class JournalTests(unittest.TestCase):
    def setUp(self):
        bind_runtime()
        import audit
        import common

        folder = TemporaryDirectory(dir=common.WORKSPACE / "cache/tmp")
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.common = common
        for module, name in (
            (audit, "ALL_RESULTS_TXT"),
            (common, "OPERATIONS"),
            (common, "ISSUES"),
        ):
            guard = patch.object(module, name, self.root / (name + ".txt"))
            guard.start()
            self.addCleanup(guard.stop)

    def test_both_lanes_use_the_same_transcript_and_source_snapshot(self):
        out = self.root / "session"
        session = AboDialogue(out, entry="PowerShell 测试")
        try:
            session.respond("B07SSCD71B@amazon.com.au 的品牌是什么")
            session.respond("B07T9TLVJ3@amazon.in 适用 iPhone 8 Plus 吗？")
            session.respond("帮我退款")
        finally:
            session.close()
            session.close()
        records = self.common.read_jsonl(out / "results.jsonl")
        self.assertEqual(len(records), 3)
        self.assertEqual([row["entry_lane"] for row in records[:2]], ["product", "compatibility"])
        transcript = (out / "对话记录.txt").read_text("utf-8")
        self.assertEqual(transcript.count("\n用户："), 3)
        self.assertIn(records[1]["display_answer"], transcript)
        self.assertEqual(
            ["start", "ready", "close"],
            [event["event"] for event in self.common.read_jsonl(out / "运行事件.jsonl")],
        )
        self.assertTrue((out / "source_snapshot/abo_journal.py").is_file())
        self.assertEqual(records[0]["config_sha256"], self.common.file_hash(out / "config.json"))
        self.assertFalse(self.common.ISSUES.exists())

    def test_issue_retains_failure_and_accepts_same_number_update(self):
        out = self.root / "failure"
        session = AboDialogue(out)
        try:
            with patch(
                "app.abo.service.present", side_effect=RuntimeError("password=private-test-phrase")
            ):
                with self.assertRaises(RuntimeError):
                    session.respond("商品列表")
        finally:
            session.close()
        event = next(
            item
            for item in self.common.read_jsonl(out / "运行事件.jsonl")
            if item["status"] == "失败"
        )
        key = event["issue_id"]
        self.common.update_issue(key, "测试注入", "撤销测试注入", "正常会话已核对", "已解决")
        issues = self.common.ISSUES.read_text("utf-8")
        self.assertIn(key + " 更新", issues)
        self.assertIn("运行记录.txt", issues)
        for path in (
            self.common.ISSUES,
            out / "运行记录.txt",
            out / "运行事件.jsonl",
            out / "对话记录.txt",
        ):
            self.assertNotIn("private-test-phrase", path.read_text("utf-8"))

    def test_remote_mirrors_evidence_and_does_not_store_connection_address(self):
        from kaggle_chat_client import main

        remote = AboDialogue(self.root / "remote")
        try:
            row = remote.respond("B07SSCD71B@amazon.com.au 的品牌是什么")
            config = (remote.output / "config.json").read_text("utf-8")
        finally:
            remote.close()
        import json

        kernel = Mock()
        kernel.execute.side_effect = [json.loads(config), row]
        local = self.root / "mirror"
        with (
            patch("builtins.input", return_value="https://test.kaggle.net/lab?token=private-token"),
            patch("kaggle_chat_client.connect", return_value=kernel),
        ):
            self.assertEqual(main(["--question", row["question"]], output=local), 0)
        (copied,) = self.common.read_jsonl(local / "results.jsonl")
        self.assertEqual(copied["origin_run_name"], "remote")
        self.assertEqual(copied["citations"], row["citations"])
        self.assertTrue((local / "config.json").is_file())
        self.assertIn(row["display_answer"], (local / "对话记录.txt").read_text("utf-8"))
        for path in local.glob("*"):
            self.assertNotIn("private-token", path.read_text("utf-8"))

    def test_redaction_keeps_product_identity_and_json_structure(self):
        path = self.root / "redacted.jsonl"
        self.common.append_log_json(
            path,
            {
                "value": "B07SSCD71B@amazon.com.au https://a.kaggle.net/lab?token=private-token",
                "error": r'password="priv\"ate"; name@example.org',
            },
        )
        (row,) = self.common.read_jsonl(path)
        self.assertIn("B07SSCD71B@amazon.com.au", row["value"])
        self.assertNotIn("private-token", row["value"])
        self.assertNotIn("name@example.org", row["error"])
        self.assertNotIn("ate", row["error"])
