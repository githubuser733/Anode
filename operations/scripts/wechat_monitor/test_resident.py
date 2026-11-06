"""Real persistence/engine tests, with fake Alpha reads and desktop notifications."""
from contextlib import closing
import json
from pathlib import Path
import unittest
import uuid
from unittest import mock

from core import ROOT, configuration
import resident

T = 1800000000000


class FakeReader:
    account = "test-account"
    def __init__(self, records=()):
        self.records, self.calls = list(records), 0
        self.fail_source = None
    def verify_sources(self):
        pass
    def messages(self, source, maximum, **kwargs):
        self.calls += 1
        if source["id"] == self.fail_source:
            raise RuntimeError("ALPHA_READ_FAILED_HTTP_503")
        found = [r for r in self.records if r["source_id"] == source["id"] and kwargs["after_ms"] < r["time_ms"] < kwargs["before_ms"]]
        if len(found) > maximum:
            raise ValueError("COVERAGE_GAP_POLL_LIMIT_REACHED")
        return found


class FakeNotifier:
    def __init__(self):
        self.available, self.unknown, self.calls = True, False, []
    def ready(self):
        if not self.available:
            raise RuntimeError("NOTIFIER_ANODE_NOT_READY")
    def show(self, title, text):
        self.calls.append((title, text))
        if self.unknown:
            raise TimeoutError("receipt lost")
        return {"state": "shown"}


class ResidentTests(unittest.TestCase):
    def setUp(self):
        self.cfg = configuration()
        # Exercise nonzero aggregation even when the live preference is immediate.
        self.cfg["resident"]["aggregateSeconds"] = 20
        base = "artifacts/wechat-resident-tests/" + uuid.uuid4().hex
        for key, folder in (("stateDirectory", "state"), ("runsDirectory", "runs"), ("reportsDirectory", "reports")):
            self.cfg["resident"][key] = base + "/" + folder
        self.path = resident.directories(self.cfg)["stateDirectory"]
        self.store = resident.Store(self.path)
        self.addCleanup(self.store.close)
        self.store.initialize(self.cfg, T)
        self.engine = resident.Engine(self.cfg, self.store)
        self.notifier = FakeNotifier()

    def message(self, identity, at, text="收 UA1000", group=0):
        source = self.cfg["sources"][group]
        return {"id": identity, "source_id": source["id"], "source_name": source["name"],
                "time_ms": at, "time": "测试时刻", "sender": "测试", "type": "文字", "text": text}

    def test_immediate_first_alert_at_two_second_poll_preserves_cooldown(self):
        self.cfg["pollSeconds"] = 2
        self.cfg["resident"]["aggregateSeconds"] = 0
        self.engine.poll(FakeReader([self.message("first", T + 1)]), T + 100)
        first = self.engine.flush(self.notifier, T + 100)
        self.assertEqual(first["state"], "shown")
        self.engine.poll(FakeReader([self.message("second", T + 200)]), T + 2100)
        self.assertIsNone(self.engine.flush(self.notifier, T + 2100))
        self.assertEqual(self.store.stats()["pendingHits"], 1)
        self.assertIsNotNone(self.engine.flush(self.notifier, T + 60100))
        self.assertEqual(len(self.notifier.calls), 2)

    def test_cx_rules_keep_raw_data_and_do_not_starve_later_eligible_hits(self):
        policy={'schemaVersion':1,'version':'test-cx','enabled':True,'unknownAction':'dashboard-only',
                'cx':{'quantityPerPerson':88000,'minimumCnyPerWan':985}}
        source=self.cfg['sources'][0]
        records=[self.message('excluded'+str(i),T+1,'收CX3万 960/万') for i in range(501)]
        records+=[self.message('review',T+2,'收CX8.8万'),self.message('eligible',T+3,'收CX8.8万 985元/万')]
        self.store.commit_poll(source['id'],records,T+100,T+100,self.cfg['keywords'])
        with mock.patch.object(resident.business_rules,'load',return_value=policy):
            receipt=self.engine.flush(self.notifier,T+30000)
        self.assertEqual(receipt['hitCount'],1)
        self.assertEqual(self.store.stats()['keywordHits'],503)
        self.assertEqual(self.store.stats(policy)['pendingHits'],0)
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM notice_items').fetchone()[0],1)
        self.assertEqual(self.store.db.execute('SELECT message_id FROM notice_items').fetchone()[0],'eligible')

    def test_initial_history_is_not_a_new_alert_and_overlap_is_deduplicated(self):
        reader = FakeReader([self.message("old", T - 1), self.message("new", T + 1)])
        self.engine.poll(reader, T + 100)
        self.engine.poll(reader, T + 200)
        self.assertEqual(self.store.stats()["newMessages"], 1)
        self.assertEqual(self.store.stats()["pendingHits"], 1)

    def test_restart_preserves_cursor_pending_and_catches_offline_messages(self):
        self.engine.poll(FakeReader([self.message("one", T + 1)]), T + 1000)
        with closing(resident.Store(self.path)) as resumed:
            resumed.initialize(self.cfg, T + 99999)
            self.assertEqual(resumed.meta("activation"), T)
            resident.Engine(self.cfg, resumed).poll(FakeReader([self.message("one", T + 1), self.message("offline", T + 2000)]), T + 3000)
            self.assertEqual(resumed.stats()["pendingHits"], 2)
            self.assertEqual(resumed.cursor(self.cfg["sources"][0]["id"]), T + 3000)

    def test_failed_group_keeps_cursor_and_other_group_progress_is_reusable(self):
        reader = FakeReader([self.message("one", T + 1), self.message("two", T + 1, group=1)])
        reader.fail_source = self.cfg["sources"][1]["id"]
        with self.assertRaisesRegex(RuntimeError, "503"):
            self.engine.poll(reader, T + 100)
        self.assertEqual(self.store.cursor(self.cfg["sources"][1]["id"]), T)
        self.assertEqual(self.store.cursor(self.cfg["sources"][0]["id"]), T + 100)
        reader.fail_source = None
        self.engine.poll(reader, T + 200)
        self.assertEqual(self.store.stats()["newMessages"], 2)

    def test_poll_overflow_does_not_silently_advance_checkpoint(self):
        self.cfg["maxPollMessagesPerGroup"] = 1
        with self.assertRaisesRegex(ValueError, "COVERAGE_GAP"):
            self.engine.poll(FakeReader([self.message("a", T + 1), self.message("b", T + 2)]), T + 100)
        self.assertEqual(self.store.cursor(self.cfg["sources"][0]["id"]), T)

    def test_aggregate_delay_cooldown_and_no_duplicate_notice(self):
        reader = FakeReader([self.message("a", T + 1), self.message("b", T + 2, group=1)])
        self.engine.poll(reader, T + 100)
        self.assertIsNone(self.engine.flush(self.notifier, T + 1000))
        first = self.engine.flush(self.notifier, T + 20101)
        self.assertEqual(first["hitCount"], 2)
        self.assertEqual(len(self.notifier.calls), 1)
        self.assertTrue(Path(first["report"]).is_file())
        reader.records.append(self.message("c", T + 21000))
        self.engine.poll(reader, T + 22000)
        self.assertIsNone(self.engine.flush(self.notifier, T + 50000))
        self.assertIsNotNone(self.engine.flush(self.notifier, T + 80102))
        self.assertIsNone(self.engine.flush(self.notifier, T + 180000))
        self.assertEqual(len(self.notifier.calls), 2)

    def test_notifier_offline_keeps_pending_for_safe_recovery(self):
        self.engine.poll(FakeReader([self.message("a", T + 1)]), T + 100)
        self.notifier.available = False
        with self.assertRaisesRegex(RuntimeError, "NOT_READY"):
            self.engine.flush(self.notifier, T + 30000)
        self.assertEqual(self.store.stats()["pendingHits"], 1)
        self.assertEqual(self.store.stats()["notificationsUncertain"], 0)
        self.notifier.available = True
        self.engine.flush(self.notifier, T + 40000)
        self.assertEqual(self.store.stats()["notificationsShown"], 1)

    def test_uncertain_popup_is_retained_but_not_blindly_replayed(self):
        self.engine.poll(FakeReader([self.message("a", T + 1)]), T + 100)
        self.notifier.unknown = True
        with self.assertRaises(TimeoutError):
            self.engine.flush(self.notifier, T + 30000)
        with closing(resident.Store(self.path)) as resumed:
            self.assertIsNone(resident.Engine(self.cfg, resumed).flush(self.notifier, T + 130000))
            self.assertEqual(resumed.stats()["notificationsUncertain"], 1)
        self.assertEqual(len(self.notifier.calls), 1)

    def test_account_change_and_source_change_are_refused(self):
        self.store.bind_account("original")
        with self.assertRaisesRegex(ValueError, "ACCOUNT_CHANGED"):
            self.store.bind_account("another")
        changed = json.loads(json.dumps(self.cfg))
        changed["keywords"] = ["其他"]
        with self.assertRaisesRegex(ValueError, "POLICY_CHANGED"):
            self.store.initialize(changed, T + 100)

    def test_wrong_group_rolls_back_the_entire_ingest(self):
        source = self.cfg["sources"][0]["id"]
        with self.assertRaisesRegex(ValueError, "WRONG_GROUP"):
            self.store.commit_poll(source, [self.message("ok", T + 1), self.message("wrong", T + 2, group=1)], T + 100, T + 100, self.cfg["keywords"])
        self.assertEqual(self.store.stats()["newMessages"], 0)
        self.assertEqual(self.store.cursor(source), T)

    def test_control_is_append_only_and_disabled_is_persistent(self):
        resident.control(self.cfg, True)
        resident.control(self.cfg, False)
        self.assertFalse(resident.enabled(self.cfg))
        self.assertEqual(len((self.path / "control.jsonl").read_text("utf-8").splitlines()), 2)

    def test_service_recreates_reader_after_connection_failure(self):
        clock = [0.0]
        calls = [0]
        def connect(config):
            calls[0] += 1
            if calls[0] == 1:
                raise ConnectionError("offline")
            return FakeReader()
        def sleep(seconds):
            clock[0] += seconds
        with mock.patch.object(resident, "enabled", side_effect=lambda c: clock[0] < 31), \
             mock.patch.object(resident, "AlphaReader", side_effect=connect), \
             mock.patch.object(resident.time, "monotonic", side_effect=lambda: clock[0]), \
             mock.patch.object(resident.time, "sleep", side_effect=sleep), \
             mock.patch.object(resident, "now_ms", side_effect=lambda: T + int(clock[0] * 1000)):
            result = resident.serve(self.cfg)
        self.assertEqual(calls[0], 2)
        self.assertEqual(result["result"], "RESIDENT_STOPPED")
        rows = [json.loads(s) for s in (self.path / "health.jsonl").read_text("utf-8").splitlines()]
        self.assertEqual(rows[0]["state"], "degraded")
        self.assertTrue(any(r["state"] == "healthy" for r in rows))
        self.assertEqual(rows[-1]["state"], "stopped")


if __name__ == "__main__":
    unittest.main(verbosity=2)
