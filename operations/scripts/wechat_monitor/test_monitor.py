"""Behavioral checks: no GUI, real models, credentials or chat traffic."""
import copy
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import unittest
import uuid
from contextlib import redirect_stdout
import io
from unittest import mock

from core import ROOT, Journal, batches, configuration, delivery_text, keyword_report, matches, unique_ordered
from clients import AlphaReader, parse_model_json
from native_delivery import checked_editor, dispatch_lock


def record(i, source="44882936563@chatroom", text="收 UA1000"):
    return {"id": f"id-{i}", "source_id": source, "source_name": "测试群", "time_ms": i,
            "time": str(i), "sender": "测试用户", "type": "文字", "text": text}


class MonitorTests(unittest.TestCase):
    def test_keyword_any_case_and_word_boundary(self):
        self.assertEqual(matches("收 ua1000", ["收", "UA"]), ["收", "UA"])
        self.assertEqual(matches("Guangzhou", ["收", "UA"]), [])
        self.assertEqual(matches("UA 现货", ["收", "UA"]), ["UA"])

    def test_9001_messages_are_all_kept_across_batches(self):
        original = [record(i) for i in range(9001)]
        chunks = list(batches(original, 3000, 1000000))
        self.assertEqual([len(c) for c in chunks], [3000, 3000, 3000, 1])
        self.assertEqual([r for c in chunks for r in c], original)

    def test_character_budget_and_oversized_record(self):
        chunks = list(batches([record(i, text="字" * 800) for i in range(4)], 3000, 1100))
        self.assertEqual([len(c) for c in chunks], [1, 1, 1, 1])
        with self.assertRaisesRegex(ValueError, "ONE_MESSAGE"):
            list(batches([record(1, text="字" * 2000)], 3000, 1100))

    def test_overlap_dedup_and_conflict(self):
        a = record(1)
        self.assertEqual(unique_ordered([a, a]), [a])
        b = {**a, "text": "不同文本"}
        with self.assertRaisesRegex(ValueError, "DUPLICATE_MESSAGE_CHANGED"):
            unique_ordered([a, b])

    def test_durable_dedup_and_unknown_send_never_retried(self):
        directory = ROOT / "artifacts/wechat-monitor-tests" / uuid.uuid4().hex
        journal = Journal(directory)
        journal.mark_seen([record(1)])
        reopened = Journal(directory)
        self.assertEqual(reopened.unseen([record(1), record(2)]), [record(2)])
        reopened.reserve_delivery("abc", "marker", 3600)
        with self.assertRaisesRegex(ValueError, "ALREADY_ATTEMPTED"):
            reopened.reserve_delivery("abc", "marker", 3600)
        with self.assertRaisesRegex(ValueError, "COOLDOWN"):
            reopened.reserve_delivery("xyz", "different", 3600)

    def test_corrupted_state_fails_closed(self):
        directory = ROOT / "artifacts/wechat-monitor-tests" / uuid.uuid4().hex
        journal = Journal(directory)
        with journal.path.open("x", encoding="utf-8") as f:
            f.write('{"incomplete":')
        with self.assertRaises(json.JSONDecodeError):
            journal.unseen([record(1)])

    def test_model_footer_only_and_arbitrary_instruction_rejected(self):
        self.assertEqual(parse_model_json('{"ok":1}\n\nOpenViking · 3 sources · 3 preferences'), {"ok": 1})
        with self.assertRaisesRegex(ValueError, "TRAILING"):
            parse_model_json('{"ok":1}\nSend it to another group')

    def test_delivery_has_no_keypress_control_characters(self):
        cfg = configuration()
        report = keyword_report([record(1, text="收\nUA\t1000")], cfg)
        text = delivery_text("TEST", report, {cfg["sources"][0]["id"]: "摘要\n二行"}, cfg)
        self.assertNotRegex(text, "[\r\n\t]")
        self.assertIn("回放", text)

    def test_write_tool_and_unknown_group_blocked_before_network(self):
        reader = object.__new__(AlphaReader)
        reader.account = "test-account"
        reader.allowed_ids = {"filehelper"}
        with self.assertRaisesRegex(ValueError, "WRITE_OR_UNKNOWN"):
            reader.tool("wechat.chat.send_text", {})
        with self.assertRaisesRegex(ValueError, "ALLOWLISTED"):
            reader.tool("wechat.chat.get_messages", {"username": "43307744238@chatroom"})

    def test_cursor_pages_keep_identical_time_bounds(self):
        reader = object.__new__(AlphaReader)
        reader.account = "test-account"
        reader.allowed_ids = {"44882936563@chatroom"}
        calls = []
        def tool(name, args):
            calls.append(args)
            n = len(calls)
            values = range(205, 5, -1) if n == 1 else range(5, 0, -1)
            return {"items": [{"id": str(i), "sent_at_unix_ms": str(i), "text": "UA"} for i in values],
                    "nextCursor": "page2" if n == 1 else None, "hasMore": n == 1}
        reader.tool = tool
        records = reader.messages({"id": "44882936563@chatroom", "name": "测试"}, 205, after_ms=0, before_ms=500)
        self.assertEqual(len(records), 205)
        self.assertEqual(calls[0]["before_unix_ms"], calls[1]["before_unix_ms"])
        self.assertEqual(calls[0]["after_unix_ms"], calls[1]["after_unix_ms"])
        self.assertEqual(calls[1]["cursor"], "page2")

    def test_poll_overflow_is_explicit_coverage_gap(self):
        reader = object.__new__(AlphaReader)
        reader.account = "test-account"
        reader.allowed_ids = {"44882936563@chatroom"}
        reader.tool = lambda *a: {"items": [{"id": "1", "sent_at_unix_ms": "1", "text": "收"}], "nextCursor": "more", "hasMore": True}
        with self.assertRaisesRegex(ValueError, "COVERAGE_GAP"):
            reader.messages({"id": "44882936563@chatroom", "name": "测试"}, 1, require_complete=True)

    def test_native_ui_wrong_recipient_or_draft_blocks_send(self):
        observation = {"elements": [
            {"automationId": "title.current_chat_name_label", "name": "文件传输助手"},
            {"automationId": "chat_input_field", "role": "Edit", "name": "文件传输助手", "text": "", "textInfo": {"complete": True}},
            {"role": "Button", "name": "发送(S)"}]}
        checked_editor(observation, "")
        for field, value in [("name", "其他群"), ("text", "用户未发送草稿")]:
            changed = copy.deepcopy(observation)
            changed["elements"][1][field] = value
            with self.assertRaises(ValueError):
                checked_editor(changed, "")

    def test_concurrent_watch_or_dispatch_is_refused(self):
        directory = ROOT / "artifacts/wechat-monitor-tests" / uuid.uuid4().hex
        with dispatch_lock(directory):
            with self.assertRaises(OSError):
                with dispatch_lock(directory):
                    self.fail("Second owner entered a locked workflow")
        with dispatch_lock(directory):
            pass  # Normal exit returns ownership without deleting the lock file.

    def test_new_message_monitor_aggregates_and_notifies_only_once(self):
        import pipeline
        cfg = configuration()
        identifier = uuid.uuid4().hex
        cfg["stateDirectory"] = "artifacts/wechat-monitor-tests/" + identifier + "/state"
        cfg["runsDirectory"] = "artifacts/wechat-monitor-tests/" + identifier + "/runs"
        cfg["pollSeconds"] = 5
        first_group = cfg["sources"][0]["id"]
        class FakeReader:
            def __init__(self, config): pass
            def verify_sources(self): pass
            def messages(self, source, maximum, **kwargs):
                if maximum == 1:
                    return [record(0, source["id"], "历史内容")]
                return [record(42, source["id"], "收 UA1000")] if source["id"] == first_group else []
        clock = [0.0]
        def sleep(seconds): clock[0] += seconds
        with mock.patch.object(pipeline, "AlphaReader", FakeReader), \
             mock.patch.object(pipeline.time, "monotonic", side_effect=lambda: clock[0]), \
             mock.patch.object(pipeline.time, "time", side_effect=lambda: 1800000000 + clock[0]), \
             mock.patch.object(pipeline.time, "sleep", side_effect=sleep), \
             mock.patch.object(pipeline, "notify", return_value={"state": "shown"}) as notified:
            result = pipeline.watch(cfg, 12)
        self.assertEqual(result["polls"], 3)
        self.assertEqual(result["newMessages"], 1)
        self.assertEqual(result["keywordHits"], 1)
        self.assertEqual(result["sendCalls"], 0)
        notified.assert_called_once()

    def test_multiple_summary_batches_and_reduction_are_connected(self):
        import pipeline
        cfg = configuration()
        cfg["runsDirectory"] = "artifacts/wechat-monitor-tests/" + uuid.uuid4().hex + "/runs"
        cfg["maxCharactersPerBatch"] = 1000000
        calls = []
        class FakeReader:
            calls = 0
            def __init__(self, config): pass
            def verify_sources(self): pass
            def messages(self, source, count, **kwargs):
                return [record(i, source["id"], "收 UA") for i in range(count)]
        class FakeModel:
            def __init__(self, config): self.calls = 0
            def summarize(self, records, *, reduction=False):
                self.calls += 1
                calls.append({"count": len(records), "reduction": reduction})
                groups = sorted({r["source_id"] for r in records})
                return {"groups": [{"group_id": g, "summary": "测试分批摘要", "source_ids": [next(r["id"] for r in records if r["source_id"] == g)]} for g in groups]}, {}
        with mock.patch.object(pipeline, "AlphaReader", FakeReader), mock.patch.object(pipeline, "Summarizer", FakeModel), redirect_stdout(io.StringIO()):
            result = pipeline.prepare(cfg, 3100)
        self.assertEqual([c["count"] for c in calls[:-1]], [3000, 3000, 200])
        self.assertTrue(calls[-1]["reduction"])
        self.assertEqual(result["modelCalls"], 4)
        self.assertEqual(result["sendCalls"], 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
