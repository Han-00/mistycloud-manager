"""流量历史/报告离线回归；全部数据和导出使用临时目录。"""
import csv
import io
import json
import os
import sys
import tempfile
import unittest
from datetime import date, timedelta
from unittest.mock import Mock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import stats
from traffic_analysis import build_report, export_report
from ui_web import WebAppUI, _JsApi

TODAY = date(2026, 10, 3)
MB = 1024 ** 2


def sample(amount, *, legacy=False):
    return {"bytes": amount, "samples": 0 if legacy else 2, "legacy": legacy}


class TrafficHistoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = os.path.join(self.tmp.name, "stats.json")
        self.stats = stats.Stats(self.path)
        self.stats._today = lambda: TODAY.isoformat()
        self.stats._hour_key = lambda: "2026-10-03T12"

    def test_increment_reset_and_account_switch_stay_consistent(self):
        s = self.stats
        for email, used in [("one", 100), ("one", 150), ("one", 150),
                            ("one", 20), ("one", 30), ("two", 1000), ("two", 1007)]:
            s.add_traffic(email, used)
        state = s.snapshot()
        self.assertEqual(state["traffic_used"], 67)
        self.assertEqual(state["daily"]["2026-10-03"]["bytes"], 67)
        self.assertEqual(state["daily"]["2026-10-03"]["samples"], 7)
        self.assertEqual(state["hourly"]["2026-10-03T12"], 67)
        self.assertEqual(stats.Stats(self.path).snapshot()["daily"], state["daily"])

    def test_calendar_rollover_and_missing_day(self):
        s = self.stats
        s.add_traffic("one", 0)
        s.add_traffic("one", 10)
        s._today = lambda: "2026-10-05"
        s._hour_key = lambda: "2026-10-05T12"
        s.add_traffic("one", 17)
        report = build_report(s.snapshot(), "2026-10-03", "2026-10-05", today=date(2026, 10, 5))
        self.assertEqual([r["bytes"] for r in report["series"]], [10, None, 7])
        self.assertEqual(report["summary"]["average_daily_bytes"], 10)
        self.assertEqual(s.get_pending()["traffic_used"], 10)

    def test_zero_measurement_is_distinct_from_missing(self):
        self.stats.add_traffic("one", 0)
        self.stats.add_traffic("one", 0)
        report = build_report(self.stats.snapshot(), "2026-10-02", "2026-10-03", today=TODAY)
        self.assertEqual([r["bytes"] for r in report["series"]], [None, 0])
        self.assertEqual(report["summary"]["total_bytes"], 0)
        self.assertIsNone(report["summary"]["average_daily_bytes"])

    def test_first_baseline_and_reset_alone_do_not_become_measured_zero(self):
        self.stats.add_traffic("one", 100 * MB)
        report = build_report(self.stats.snapshot(), "2026-10-03", "2026-10-03", today=TODAY)
        self.assertIsNone(report["summary"]["total_bytes"])
        self.assertTrue(report["series"][0]["baseline_only"])
        # 再次采到相同累计值，才可以确认间隔内新增用量为0。
        self.stats.add_traffic("one", 100 * MB)
        self.assertEqual(build_report(self.stats.snapshot(), "2026-10-03", "2026-10-03", today=TODAY)["summary"]["total_bytes"], 0)
        self.stats._today = lambda: "2026-10-04"
        self.stats._hour_key = lambda: "2026-10-04T12"
        self.stats.add_traffic("one", 0)  # 只有计数器回退，不算实测零值。
        report = build_report(self.stats.snapshot(), "2026-10-04", "2026-10-04", today=date(2026,10,5))
        self.assertIsNone(report["summary"]["average_daily_bytes"])
        self.assertTrue(report["series"][0]["baseline_only"])
        self.assertEqual(report["comparison"]["status"], "incomplete")

    def test_legacy_hour_migration_is_idempotent_and_preserves_daily(self):
        payload = {"hourly": {"2026-10-01T10": 15, "2026-10-01T11": 25,
                              "2026-10-02T12": 60, "invalid": 300,
                              "2026-10-03T15": -4},
                   "daily": {"2026-10-02": sample(80)}}
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(payload, f)
        migrated = stats.Stats(self.path)
        self.assertEqual(migrated.snapshot()["daily"]["2026-10-01"], sample(40, legacy=True))
        self.assertEqual(migrated.snapshot()["daily"]["2026-10-02"]["bytes"], 80)
        migrated._save_locked()
        self.assertEqual(stats.Stats(self.path).snapshot()["daily"], migrated.snapshot()["daily"])

    def test_daily_retention_independent_from_hourly(self):
        s = self.stats
        keep = (TODAY - timedelta(days=364)).isoformat()
        expired = (TODAY - timedelta(days=365)).isoformat()
        s.data["daily"] = {keep: sample(10), expired: sample(20), "2026-08-01": sample(30)}
        s.data["hourly"] = {"2026-08-01T12": 30}
        s.add_traffic("one", 0)
        snapshot = s.snapshot()
        self.assertIn(keep, snapshot["daily"])
        self.assertNotIn(expired, snapshot["daily"])
        self.assertIn("2026-08-01", snapshot["daily"])
        self.assertNotIn("2026-08-01T12", snapshot["hourly"])

    def test_corrupt_existing_day_can_recover_from_legacy_hourly(self):
        with open(self.path,"w",encoding="utf-8") as f:
            json.dump({"daily":{"2026-10-01":"bad"}, "hourly":{"2026-10-01T12":42}},f)
        recovered=stats.Stats(self.path).snapshot()["daily"]["2026-10-01"]
        self.assertEqual(recovered["bytes"],42)
        self.assertTrue(recovered["legacy"])

    def test_corrupt_daily_bucket_does_not_break_existing_statistics(self):
        self.stats.data["daily"]["2026-10-03"] = "bad"
        self.stats.add_traffic("one", 0)
        self.stats.data["daily"]["2026-10-03"]["samples"] = "invalid"
        self.stats.add_traffic("one", 100)
        self.assertEqual(self.stats.snapshot()["traffic_used"], 100)
        self.assertEqual(self.stats.snapshot()["daily"]["2026-10-03"]["bytes"], 100)

    def test_snapshot_and_instances_do_not_share_daily_values(self):
        self.stats.add_traffic("one", 0)
        state = self.stats.snapshot()
        state["daily"]["2026-10-03"]["bytes"] = 999
        other = stats.Stats(os.path.join(self.tmp.name, "other.json"))
        self.assertEqual(self.stats.snapshot()["daily"]["2026-10-03"]["bytes"], 0)
        self.assertEqual(other.snapshot()["daily"], {})


class TrafficReportTests(unittest.TestCase):
    def test_empty_has_null_totals_and_closed_calendar_interval(self):
        report = build_report({}, "2026-09-27", "2026-10-03", today=TODAY)
        self.assertEqual(len(report["series"]), 7)
        self.assertIsNone(report["summary"]["total_bytes"])
        self.assertEqual(report["summary"]["missing_days"], 7)

    def test_average_excludes_today_and_missing_but_includes_measured_zero(self):
        report = build_report({"daily": {"2026-09-30": sample(20), "2026-10-01": sample(0),
                                        "2026-10-03": sample(900)}},
                              "2026-09-30", "2026-10-03", today=TODAY)
        self.assertEqual(report["summary"]["total_bytes"], 920)
        self.assertEqual(report["summary"]["average_daily_bytes"], 10)
        self.assertEqual(report["summary"]["recorded_days"], 3)

    def test_comparison_requires_complete_equal_periods_and_excludes_today(self):
        daily = {"2026-09-27": sample(10), "2026-09-28": sample(10), "2026-09-29": sample(10),
                 "2026-09-30": sample(20), "2026-10-01": sample(20), "2026-10-02": sample(20),
                 "2026-10-03": sample(999)}
        report = build_report({"daily": daily}, "2026-09-30", "2026-10-03", today=TODAY)
        self.assertEqual(report["comparison"]["percent"], 100)
        self.assertEqual(report["comparison"]["days"], 3)
        self.assertEqual(report["comparison"]["previous_start"], "2026-09-27")
        del daily["2026-09-28"]
        report = build_report({"daily": daily}, "2026-09-30", "2026-10-03", today=TODAY)
        self.assertEqual(report["comparison"]["status"], "incomplete")
        self.assertIsNone(report["comparison"]["percent"])

    def test_zero_comparison_baseline_is_not_infinite_growth(self):
        report = build_report({"daily": {"2026-10-01": sample(0), "2026-10-02": sample(100)}},
                              "2026-10-02", "2026-10-02", today=TODAY)
        self.assertEqual(report["comparison"]["status"], "zero_baseline")
        self.assertIsNone(report["comparison"]["percent"])

    def test_peak_marks_today_and_analysis_stays_concise(self):
        daily = {(TODAY-timedelta(days=i)).isoformat(): sample(100*MB) for i in range(1, 9)}
        daily["2026-10-01"] = sample(600*MB)
        daily["2026-10-03"] = sample(900*MB)
        report = build_report({"daily": daily}, "2026-09-25", "2026-10-03", today=TODAY)
        self.assertEqual(report["summary"]["peak"]["date"], "2026-10-03")
        self.assertTrue(report["summary"]["peak"]["partial"])
        self.assertLessEqual(len(report["insights"]), 2)

    def test_bad_future_and_expired_data_do_not_poison_report(self):
        daily = {"2026-10-01": sample(10), "invalid": sample(50), "2026-10-02": "bad",
                 "2026-10-03": sample(-4), "2026-10-04": sample(999),
                 "2025-10-02": sample(500), "2026-09-30": {"bytes": "bad", "samples": 1}}
        report = build_report({"daily": daily}, "2026-09-30", "2026-10-03", today=TODAY)
        self.assertEqual(report["summary"]["total_bytes"], 10)
        self.assertEqual(report["history_start"], "2026-10-01")

    def test_date_validation_and_365_day_bound(self):
        for first,last in [("2026-10-03","2026-10-02"), ("2026-10-01","2026-10-04"),
                           ("2026-02-30","2026-10-03"), ("2025-10-02","2026-10-03"),
                           (123,"2026-10-03"), ("20261001","2026-10-03")]:
            with self.subTest(first=first,last=last), self.assertRaises(ValueError):
                build_report({},first,last,today=TODAY)
        self.assertEqual(len(build_report({},"2025-10-04","2026-10-03",today=TODAY)["series"]),365)

    def test_csv_preserves_missing_zero_and_statistics_scope(self):
        report = build_report({"daily":{"2026-10-02":sample(0)}},"2026-10-01","2026-10-03",today=TODAY)
        rows = list(csv.reader(io.StringIO(export_report(report))))
        self.assertEqual(rows[1][1], "")
        self.assertEqual(rows[2][1], "0")
        self.assertEqual(rows[1][3], "无记录")
        self.assertIn("平台累计已用量", rows[2][5])
        self.assertNotIn("traffic_base", export_report(report))


class TrafficBridgeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.instance = stats.Stats(os.path.join(self.tmp.name,"stats.json"))
        self.instance.add_traffic("private-account",0)
        self.instance.add_traffic("private-account",10)
        self.patch = patch.object(stats,"get",return_value=self.instance)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.ui = object.__new__(WebAppUI)
        self.ui.window = Mock()
        self.api = _JsApi(self.ui)
        self.day = date.today().isoformat()

    def test_bridge_reads_history_and_errors_without_breaking_ui(self):
        self.assertEqual(self.api.get_traffic_report(self.day,self.day)["summary"]["total_bytes"],10)
        self.assertFalse(self.api.get_traffic_report("invalid",self.day)["ok"])
        with patch.object(stats,"get",side_effect=OSError("broken")):
            self.assertFalse(self.api.get_traffic_report(self.day,self.day)["ok"])

    def test_native_export_and_cancel_are_isolated(self):
        filename = os.path.join(self.tmp.name,"report.csv")
        self.ui.window.create_file_dialog.return_value = (filename,)
        result = self.api.export_traffic_report(self.day,self.day)
        self.assertTrue(result["ok"])
        with open(filename,"r",encoding="utf-8-sig") as f:
            content = f.read()
        self.assertIn("10",content)
        self.assertNotIn("private-account",content)
        self.ui.window.create_file_dialog.return_value = None
        self.assertTrue(self.api.export_traffic_report(self.day,self.day)["cancelled"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
