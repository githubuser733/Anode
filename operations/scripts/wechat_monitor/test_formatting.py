"""Formatting and pre-dispatch contract tests; never network, type, or send."""
import json
from pathlib import Path
import unittest
import uuid
from unittest import mock

from core import ROOT, configuration, digest, Journal, keyword_report, save_new
from formatting import excerpt, report_markdown
from native_delivery import validated_delivery
from pipeline import reformat, write_outputs


class FormattingTests(unittest.TestCase):
    def fixture(self):
        cfg = configuration()
        cfg["runsDirectory"] = "artifacts/wechat-monitor-tests/" + uuid.uuid4().hex
        run = ROOT / cfg["runsDirectory"] / "source"
        run.mkdir(parents=True)
        records = [{"id": "id1", "source_id": cfg["sources"][0]["id"], "time_ms": 1,
                    "time": "11-04 16:00", "sender": "用户", "text": "收 UA，10万@1.38。", "type": "文字"}]
        reports = keyword_report(records, cfg)
        summaries = {cfg["sources"][0]["id"]: "收UA 10万@1.38，仅限当日；另有报价尚待确认。"}
        return cfg, run, records, reports, summaries

    def test_excerpt_keeps_price_unit_and_condition_together(self):
        self.assertEqual(excerpt("收UA 10万@1.38，仅限当日；后续条目。", 20), "收UA 10万@1.38，仅限当日；")
        self.assertEqual(excerpt("收UA " + "限当日" * 40 + "；", 30), "本段较长，完整摘要已保存为 Markdown。")
        self.assertEqual(excerpt("-10.5 CNY；", 20), "-10.5 CNY；")

    def test_markdown_has_groups_and_statistics_and_literal_source_text(self):
        cfg, run, records, reports, summaries = self.fixture()
        reports[0]["hits"][0]["text"] = "<img src=x> ![图](https://bad.example) 收UA"
        markdown = report_markdown("TEST", reports, summaries, cfg, records, "2026-11-04", True)
        self.assertIn("## 统计", markdown)
        self.assertIn("### 完整摘要", markdown)
        self.assertIn("不是实时新消息提醒", markdown)
        self.assertNotIn("<img", markdown)
        self.assertNotIn("![图](", markdown)
        self.assertIn("&lt;img", markdown)

    def test_short_native_text_and_readable_preview_share_report(self):
        cfg, run, records, reports, summaries = self.fixture()
        write_outputs(run, cfg, records, reports, summaries, "TEST", "2026-11-04")
        task, text = validated_delivery(run.resolve(), cfg)
        self.assertLessEqual(len(text), 360)
        self.assertNotRegex(text, "[\r\n\t]")
        self.assertNotIn("命中样例", text)
        self.assertIn("\n\n", (run / "preview.txt").read_text("utf-8"))
        self.assertFalse(task["markdownAttached"])

    def test_legacy_unformatted_delivery_is_rejected(self):
        cfg, run, records, reports, summaries = self.fixture()
        save_new(run / "delivery.json", {"recipient": cfg["destination"]})
        (run / "delivery.txt").write_text("旧长消息", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "REFORMAT_REQUIRED"):
            validated_delivery(run.resolve(), cfg)

    def test_long_or_multiline_text_is_rejected_before_desktop(self):
        for body in ("长" * 361, "第一行\n第二行"):
            cfg, run, records, reports, summaries = self.fixture()
            save_new(run / "delivery.json", {"outputFormat": "brief-plus-markdown-v1", "recipient": cfg["destination"],
                "sourceIds": [s["id"] for s in cfg["sources"]], "textSha256": digest(body)})
            with (run / "delivery.txt").open("x", encoding="utf-8") as output:
                output.write(body)
            with self.assertRaisesRegex(ValueError, "UNSAFE"):
                validated_delivery(run.resolve(), cfg)

    def test_reformat_uses_saved_data_and_retains_send_identity(self):
        cfg, run, records, reports, summaries = self.fixture()
        write_outputs(run, cfg, records, reports, summaries, "TEST-ONCE", "2026-11-04")
        save_new(run / "messages.json", records)
        save_new(run / "summary-batch-001.json", {"summary": {"groups": [{"group_id": k, "summary": v, "source_ids": ["id1"]} for k, v in summaries.items()]}})
        with mock.patch("pipeline.AlphaReader", side_effect=AssertionError("No chat reads")), mock.patch("pipeline.Summarizer", side_effect=AssertionError("No model calls")):
            output = reformat(cfg, run)
        self.assertEqual((output["modelCalls"], output["alphaReadCalls"], output["sendCalls"]), (0, 0, 0))
        task, text = validated_delivery(Path(output["run"]), cfg)
        self.assertEqual(task["marker"], "TEST-ONCE")
        journal = Journal(run.parent / "state")
        journal.reserve_delivery("old-body", "TEST-ONCE", 0)
        with self.assertRaisesRegex(ValueError, "ALREADY_ATTEMPTED"):
            journal.reserve_delivery(digest(text), task["marker"], 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
