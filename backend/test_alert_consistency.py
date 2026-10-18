"""Regression checks for dashboard feedback; no database or network required."""
import unittest
from unittest.mock import MagicMock, patch

from backend import nws, reports, wildlife


class AlertConsistencyTests(unittest.TestCase):
    def test_wildlife_quality_accepts_precise_wild_records_only(self):
        good = {"captive": False, "obscured": False, "quality_grade": "research",
                "positional_accuracy": 13, "geojson": {"type": "Point", "coordinates": [-81, 29]}}
        self.assertTrue(wildlife.usable_location(good))
        for change in ({"captive": True}, {"obscured": True}, {"quality_grade": "casual"},
                       {"positional_accuracy": None}, {"positional_accuracy": 1000},
                       {"geoprivacy": "obscured"}, {"taxon_geoprivacy": "private"}):
            with self.subTest(change=change):
                self.assertFalse(wildlife.usable_location({**good, **change}))

    def test_cancel_notice_is_never_inserted_as_a_hazard(self):
        conn = MagicMock()
        cancel = {"properties": {"id": "notice", "messageType": "Cancel"}}
        with patch.object(nws, "get", return_value={"features": [cancel]}), \
                patch.object(nws, "end_alerts", return_value=1) as end:
            nws.sync_alerts(conn)
        self.assertFalse(any(call.args[0].startswith("INSERT INTO nws_alerts")
                             for call in conn.execute.call_args_list))
        end.assert_called_once_with(conn, ["notice"])

    def test_cancellation_wins_regardless_of_feed_order(self):
        alert = {"geometry": None, "properties": {
            "id": "original", "sent": "2026-10-18T00:00:00Z", "event": "Wind Advisory",
            "headline": "Wind Advisory", "geocode": {"UGC": ["FLZ125"]}}}
        cancel = {"properties": {"id": "cancel", "messageType": "Cancel",
                                 "references": [{"identifier": "original"}]}}
        conn = MagicMock()
        conn.execute.return_value = MagicMock()
        active = set()

        def execute(sql, args=None):
            if sql.startswith("INSERT INTO nws_alerts"):
                active.add(args[0])
            return MagicMock()

        def end_alerts(connection, ids):
            active.difference_update(ids)
            return len(ids)

        conn.execute.side_effect = execute
        with patch.object(nws, "get", return_value={"features": [cancel, alert]}), \
                patch.object(nws, "end_alerts", side_effect=end_alerts):
            nws.sync_alerts(conn)
        self.assertNotIn("original", active)

    def test_obscured_wildlife_does_not_become_precise_site_alert(self):
        conn = MagicMock()
        conn.execute.return_value.fetchone.side_effect = [
            {"s": 25, "w": -85, "n": 35, "e": -80}, {"d": 3}, {"count": 0}]
        observation = {"id": 1, "geojson": {"type": "Point", "coordinates": [-84.39, 33.76]},
                       "observed_on": "2026-10-18", "taxon": {"name": "Alligator mississippiensis"},
                       "obscured": True, "quality_grade": "research", "captive": False,
                       "positional_accuracy": 13}
        response = MagicMock()
        response.json.return_value = {"results": [observation], "total_results": 1}
        with patch.object(wildlife, "TAXA", ["Alligator mississippiensis"]), \
                patch.object(wildlife.client, "get", return_value=response):
            wildlife.sync(conn)
        rows = conn.cursor.return_value.executemany.call_args.args[1]
        self.assertEqual(rows, [])

    def test_current_alert_is_not_described_as_driver_of_tomorrows_low_score(self):
        facts = {"site": {"name": "Plant A"}, "recommendation": {"value": "caution"},
                 "assumptions": [], "S2_active_alerts": [
                     {"category": "wildlife", "level": "warning", "message": "Crew report: Stuff"}],
                 "S1_hazard_scores": {"next_day": {"score": 0.0, "category_scores": {"_meta": {}}}}}
        report = reports.template(facts)
        self.assertNotIn("biggest drivers are Crew report", report["summary"])
        self.assertIn("tomorrow", report["summary"].lower())
        self.assertIn("unverified", report["hazards"][0]["explanation"].lower())


if __name__ == "__main__":
    unittest.main()
