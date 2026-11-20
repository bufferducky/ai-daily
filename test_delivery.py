import io
import json
import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

import delivery_gate
import notify
import wait_for_today


TZ = ZoneInfo("Asia/Shanghai")


def at(value):
    return datetime.fromisoformat("2026-11-19T" + value).replace(tzinfo=TZ)


class GateTests(unittest.TestCase):
    def gate(self, time, **kwargs):
        with patch.object(delivery_gate, "already_notified", return_value=kwargs.pop("sent", False)) as lookup:
            result = delivery_gate.decide(at(time), event="schedule", **kwargs)
        return result, lookup

    def test_window_start_and_end(self):
        for time, expected in [("08:29:59", False), ("08:30:00", True),
                               ("09:59:59", True), ("10:00:00", False), ("10:10:00", False)]:
            with self.subTest(time=time):
                result, lookup = self.gate(time)
                self.assertEqual(result, expected)
                self.assertEqual(lookup.called, expected)

    def test_success_skips_subsequent_attempts(self):
        result, _ = self.gate("09:00:00", sent=True)
        self.assertFalse(result)

    def test_failed_attempt_can_retry(self):
        result, _ = self.gate("09:00:00", sent=False)
        self.assertTrue(result)

    def test_manual_force_and_dry_run_bypass_window_and_receipt(self):
        for options in [{"force": True}, {"dry": True}]:
            with patch.object(delivery_gate, "already_notified") as lookup:
                self.assertTrue(delivery_gate.decide(at("11:00:00"), event="workflow_dispatch", **options))
                lookup.assert_not_called()

    def test_external_automatic_mode_enforces_window_even_with_force_or_dry(self):
        for time in ["08:29:59", "10:00:00", "11:00:00"]:
            with patch.object(delivery_gate, "already_notified") as lookup:
                self.assertFalse(delivery_gate.decide(
                    at(time), event="workflow_dispatch", automatic=True, force=True, dry=True,
                ))
                lookup.assert_not_called()

    def test_external_automatic_mode_waits_and_respects_receipt(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "output"
            with patch.dict(os.environ, {
                "GITHUB_EVENT_NAME": "workflow_dispatch", "AUTOMATIC": "true",
                "GITHUB_OUTPUT": str(output), "FORCE": "false", "DRY": "false",
            }), patch.object(delivery_gate, "datetime") as clock, \
                 patch.object(delivery_gate, "already_notified", return_value=False):
                clock.now.return_value = at("08:30:00")
                delivery_gate.main()
            self.assertIn("automatic=true\n", output.read_text())
            self.assertIn("run=true\n", output.read_text())
        with patch.object(delivery_gate, "already_notified", return_value=True):
            self.assertFalse(delivery_gate.decide(
                at("09:00:00"), event="workflow_dispatch", automatic=True,
            ))

    def test_only_live_receipt_counts(self):
        for artifacts, expected in [([], False), ([{"expired": True}], False),
                                    ([{"expired": False}], True)]:
            response = io.BytesIO(json.dumps({"artifacts": artifacts}).encode())
            with patch.dict(os.environ, {"GITHUB_TOKEN": "test", "GITHUB_REPOSITORY": "owner/repo"}), \
                 patch.object(delivery_gate.urllib.request, "urlopen", return_value=response):
                self.assertEqual(delivery_gate.already_notified("2026-11-19"), expected)


class WaitTests(unittest.TestCase):
    def run_wait(self, times, dates):
        with patch.object(wait_for_today, "datetime") as clock, \
             patch.object(wait_for_today, "latest_issue_date", side_effect=dates) as fetch, \
             patch.object(wait_for_today.time, "sleep") as sleep, \
             patch.dict(os.environ, {"GITHUB_EVENT_NAME": "schedule"}):
            clock.now.side_effect = [at(value) for value in times]
            result = wait_for_today.main()
        return result, fetch, sleep

    def test_retries_after_one_minute_then_stops_on_success(self):
        result, fetch, sleep = self.run_wait(
            ["08:30:00", "08:30:00", "08:30:00", "08:31:00", "08:31:00"],
            ["2026-11-18", "2026-11-19"],
        )
        self.assertEqual(result, 0)
        self.assertEqual(fetch.call_count, 2)
        sleep.assert_called_once_with(60)

    def test_deadline_prevents_fetch_even_if_today_is_available(self):
        result, fetch, sleep = self.run_wait(["10:00:00", "10:00:00"], ["2026-11-19"])
        self.assertEqual(result, 1)
        fetch.assert_not_called()
        sleep.assert_not_called()

    def test_response_arriving_after_deadline_is_rejected(self):
        result, _, sleep = self.run_wait(
            ["09:59:59", "09:59:59", "10:00:01"], ["2026-11-19"],
        )
        self.assertEqual(result, 1)
        sleep.assert_not_called()

    def test_final_sleep_stops_at_deadline(self):
        result, _, sleep = self.run_wait(
            ["09:59:40", "09:59:40", "09:59:40", "10:00:00"], ["2026-11-18"],
        )
        self.assertEqual(result, 1)
        sleep.assert_called_once_with(20)


class NotifyTests(unittest.TestCase):
    def test_scheduled_notification_does_not_send_after_deadline(self):
        with tempfile.TemporaryDirectory() as directory:
            latest = Path(directory) / "latest.json"
            latest.write_text(json.dumps({"date": "2026-11-19"}))
            with patch.object(notify, "LATEST", latest), \
                 patch.object(notify, "datetime") as clock, \
                 patch.object(notify, "post_feishu") as send, \
                 patch.dict(os.environ, {"GITHUB_EVENT_NAME": "schedule", "FEISHU_WEBHOOK": "test"}), \
                 patch("sys.argv", ["notify.py"]):
                clock.now.return_value = at("10:00:00")
                self.assertNotEqual(notify.main(), 0)
                send.assert_not_called()

    def test_external_automatic_notification_does_not_send_after_deadline(self):
        # 同样验证 --force 不能让自动任务越过截止时间。
        with tempfile.TemporaryDirectory() as directory:
            latest = Path(directory) / "latest.json"
            latest.write_text(json.dumps({"date": "2026-11-19"}))
            with patch.object(notify, "LATEST", latest), \
                 patch.object(notify, "datetime") as clock, \
                 patch.object(notify, "post_feishu") as send, \
                 patch.dict(os.environ, {"GITHUB_EVENT_NAME": "workflow_dispatch", "AUTOMATIC": "true"}), \
                 patch("sys.argv", ["notify.py", "--force"]):
                clock.now.return_value = at("10:00:00")
                self.assertNotEqual(notify.main(), 0)
                send.assert_not_called()


if __name__ == "__main__":
    unittest.main()
