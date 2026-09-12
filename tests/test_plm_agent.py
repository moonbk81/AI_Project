"""No PLM, backend, or LLM connection is made by these tests."""
import json
from pathlib import Path
import tempfile
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from plm_agent import Worker, load_config, locked_state, schedule_slot, main


class FakeBackend:
    def __init__(self):
        self.calls = []
        self.content = "call drop"
        self.local_test = False
        self.job = {"status": "done", "current_file": "dump_P1_payload.json"}
        self.scan = {"status": "done", "log_candidates": [
            {"file_id": "F1", "route": ["dumpstate.log"], "path": "dumpstate.log",
             "size": 100, "group": "", "kind": "log", "recommended": True,
             "recommendation_score": 0.9}
        ]}
        self.answer = {"answer": "원인 가설: IMS 오류. 추가 확인 필요.", "ids": ["log-1"], "references": []}
        self.answers = []
        self.write_error = False

    def call(self, route, payload=None):
        self.calls.append((route, payload))
        if route == "/plm/local-test":
            return {"enabled": self.local_test}
        if route == "/plm/quick-search":
            return {"defect_codes": ["P1"], "defects": [], "truncated": False}
        if route == "/plm/defects":
            return {"defects": [{"defectCode": "P1", "content": self.content, "plmStatus": "Open"}]}
        if route == "/plm/files":
            return {"files": [{"fileId": "F1", "docId": "D1", "title": "dumpstate.log"}]}
        if route == "/plm/defect-history/comments":
            return {"comments": []}
        if route == "/plm/attachments/analyze":
            return {"job_id": "J1"}
        if route == "/plm/attachments/logs":
            return {"job_id": "JSCAN"}
        if route == "/jobs/JSCAN":
            return dict(self.scan)
        if route == "/jobs/J1":
            return dict(self.job)
        if route == "/plm/analysis-query":
            return {"query": "분석해 주세요"}
        if route == "/ask":
            if self.answers:
                return dict(self.answers.pop(0))
            return dict(self.answer)
        if route == "/plm/comment":
            if self.write_error:
                raise TimeoutError("response lost")
            return {"success": True}
        raise AssertionError(route)

    def count(self, route):
        return sum(call[0] == route for call in self.calls)


class AgentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        source = Path(__file__).resolve().parents[1] / "plm/agent.example.json"
        config = json.loads(source.read_text())
        config.update(knox_id="writer", main_owner_id="owner", poll_seconds=0.001)
        config_path = Path(self.tmp.name) / "config.json"
        config_path.write_text(json.dumps(config))
        self.config = load_config(config_path)
        self.context = locked_state(self.config)
        self.db = self.context.__enter__()
        self.addCleanup(self.context.__exit__, None, None, None)
        self.api = FakeBackend()
        self.worker = Worker(self.config, self.db, self.api)

    def test_draft_is_persistent_and_repeated_run_does_not_analyze_or_post(self):
        self.assertEqual(self.worker.run(), 0)
        entry_id = self.db.execute("SELECT id FROM entries").fetchone()[0]
        entry = self.worker.get(entry_id)
        self.assertTrue(Path(entry["draft_path"]).is_file())
        Worker(self.config, self.db, self.api).run()
        self.assertEqual(self.api.count("/plm/attachments/analyze"), 1)
        self.assertEqual(self.api.count("/plm/comment"), 0)
        ask = next(payload for route, payload in self.api.calls if route == "/ask")
        self.assertEqual(ask["current_file"], self.api.job["current_file"])

    def test_explicit_publish_only_once(self):
        entry = self.worker.process("P1")
        self.worker.publish(entry)
        self.worker.run()
        self.assertEqual(self.api.count("/plm/comment"), 1)
        with self.assertRaises(ValueError):
            self.worker.publish(entry)

    def test_changed_input_creates_new_draft_and_stale_draft_cannot_publish(self):
        first = self.worker.process("P1")
        self.api.content = "new evidence"
        with self.assertRaisesRegex(RuntimeError, "변경"):
            self.worker.publish(first)
        second = self.worker.process("P1")
        self.assertNotEqual(first["id"], second["id"])
        self.assertEqual(self.api.count("/plm/comment"), 0)

    def test_uncertain_post_survives_restart_and_blocks_new_versions(self):
        self.config["mode"] = "auto"
        self.api.write_error = True
        self.assertEqual(self.worker.run(), 1)
        self.api.content = "changed"
        self.assertEqual(Worker(self.config, self.db, self.api).run(), 1)
        self.assertEqual(self.api.count("/plm/comment"), 1)
        self.assertEqual(self.db.execute("SELECT status FROM entries").fetchone()[0], "publishing")

    def test_environment_mismatch_blocks_all_work(self):
        self.api.local_test = True
        with self.assertRaisesRegex(RuntimeError, "테스트"):
            self.worker.run()
        self.assertEqual(self.api.count("/plm/quick-search"), 0)

    def test_no_logs_never_asks_or_posts(self):
        self.api.job["current_file"] = None
        self.assertEqual(self.worker.process("P1")["status"], "no_logs")
        self.assertEqual(self.api.count("/ask"), 0)

    def test_partial_logs_never_produce_comment_and_are_retried(self):
        self.api.job["skipped_logs"] = ["part.zip failed"]
        self.assertEqual(self.worker.run(), 1)
        self.assertEqual(self.api.count("/ask"), 0)
        self.api.job["skipped_logs"] = []
        self.assertEqual(self.worker.run(), 0)
        self.assertEqual(self.api.count("/plm/attachments/analyze"), 2)

    def test_failed_job_can_be_retried(self):
        self.api.job = {"status": "error", "error": "download failed"}
        self.assertEqual(self.worker.run(), 1)
        self.api.job = {"status": "done", "current_file": "dump_payload.json"}
        self.assertEqual(self.worker.run(), 0)
        self.assertEqual(self.api.count("/plm/attachments/analyze"), 2)

    def test_wait_timeout_resumes_same_job(self):
        with patch.object(self.worker, "wait_job", side_effect=TimeoutError("waiting")):
            self.assertEqual(self.worker.run(), 1)
        self.assertEqual(self.worker.run(), 0)
        self.assertEqual(self.api.count("/plm/attachments/analyze"), 1)

    def test_empty_evidence_never_posts(self):
        self.api.answer["ids"] = []
        self.config["mode"] = "auto"
        self.assertEqual(self.worker.run(), 1)
        self.assertEqual(self.api.count("/plm/comment"), 0)

    def test_recommendation_expands_to_all_logs_when_evidence_is_empty(self):
        self.api.scan["log_candidates"].append({
            "file_id": "F1", "route": ["trace.pcap"], "path": "trace.pcap",
            "size": 50, "group": "", "kind": "capture", "recommended": False,
            "recommendation_score": 0.42,
        })
        self.api.answers = [
            {"answer": "근거 없음", "ids": []},
            {"answer": "패킷 근거를 포함한 원인 가설", "ids": ["packet-1"]},
        ]
        entry = self.worker.process("P1")
        self.assertTrue(entry["expanded"])
        self.assertEqual(len(entry["selected_logs"]), 2)
        self.assertEqual(self.api.count("/plm/attachments/analyze"), 2)

    def test_llm_error_returned_as_http_success_never_posts(self):
        self.api.answer["answer"] = "LLM 추론 중 에러가 발생했습니다: model unavailable"
        self.config["mode"] = "auto"
        self.assertEqual(self.worker.run(), 1)
        self.assertEqual(self.api.count("/plm/comment"), 0)

    def test_lock_rejects_second_process(self):
        with self.assertRaisesRegex(RuntimeError, "이미 실행"):
            with locked_state(self.config):
                pass

    def test_schedule_korean_time_weekday_and_catchup(self):
        self.config["schedule"]["enabled"] = True
        before = datetime(2026, 9, 14, 23, 59, tzinfo=timezone.utc)  # Tue 08:59 KST
        after = datetime(2026, 9, 15, 0, 0, tzinfo=timezone.utc)
        self.assertIsNone(schedule_slot(self.config, before))
        slot = schedule_slot(self.config, after)
        self.assertIsNotNone(slot)
        self.assertEqual(slot, schedule_slot(self.config, after.replace(hour=2)))
        self.assertIsNone(schedule_slot(self.config, datetime(2026, 9, 12, 2, tzinfo=timezone.utc)))

    def test_completed_schedule_slot_survives_watch_restart(self):
        other = dict(self.config, state_dir=str(Path(self.tmp.name) / "watch"))
        other["schedule"] = {**other["schedule"], "enabled": True}
        with patch("plm_agent.load_config", return_value=other), \
                patch("plm_agent.schedule_slot", return_value="same-day-slot"), \
                patch.object(Worker, "run", return_value=0) as run, \
                patch("plm_agent.time.sleep", side_effect=KeyboardInterrupt):
            for _ in range(2):
                with self.assertRaises(KeyboardInterrupt):
                    main(["--config", "unused", "watch"])
        self.assertEqual(run.call_count, 1)

    def test_confirm_not_published_restores_draft_without_sending(self):
        entry = self.worker.process("P1")
        self.worker.save(entry, "publishing")
        with patch("plm_agent.load_config", return_value=self.config), \
                patch("plm_agent.locked_state") as state:
            state.return_value.__enter__.return_value = self.db
            self.assertEqual(main(["--config", "unused", "confirm-not-published", entry["id"]]), 0)
        self.assertEqual(self.worker.get(entry["id"])["status"], "draft")
        self.assertEqual(self.api.count("/plm/comment"), 0)


if __name__ == "__main__":
    unittest.main()
