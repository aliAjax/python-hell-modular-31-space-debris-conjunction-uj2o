import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from src.repository import Repository
from src.service import Service
from src.domain import DomainError


def make_item(service, operators=("Org-A", "Org-B"), tca="2026-10-01T12:00:00+00:00"):
    return service.create_item({
        "primary_object_id": "SAT-1",
        "secondary_object_id": "DEB-9",
        "tca": tca,
        "miss_distance_m": 120,
        "covariance_m": 100,
        "fuel_budget_m_s": 5,
        "track_age_hours": 1,
        "operating_organizations": list(operators),
    }, "analyst-1", "analyst")


def assess(service, item, hours=18):
    return service.act(item["id"], "assess", {"hours_to_tca": hours}, "analyst-1", "analyst", item["version"])


class CoordinationRulesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.repo = Repository(self.tmp.name)
        self.repo.initialize()
        self.service = Service(self.repo)

    def tearDown(self):
        os.unlink(self.tmp.name)

    def test_late_old_revision_does_not_override_current_state(self):
        item = assess(self.service, make_item(self.service))
        item = self.service.act(item["id"], "report_revision", {
            "observed_at": "2026-09-30T10:00:00+00:00",
            "miss_distance_m": 80,
            "covariance_m": 90,
            "source": "tracking-station-2",
        }, "analyst-1", "analyst", item["version"])
        self.assertEqual(item["payload"]["miss_distance_m"], 80)
        self.assertEqual(item["payload"]["latest_observed_at"], "2026-09-30T10:00:00+00:00")

        # 晚到的旧观测：只进来源台账，当前距离和风险保持不变。
        item = self.service.act(item["id"], "report_revision", {
            "observed_at": "2026-09-30T08:00:00+00:00",
            "miss_distance_m": 5,
            "covariance_m": 10,
            "source": "tracking-station-1-late",
        }, "analyst-1", "analyst", item["version"])
        self.assertEqual(item["payload"]["miss_distance_m"], 80)
        self.assertEqual(item["payload"]["covariance_m"], 90)
        self.assertEqual(item["payload"]["latest_observed_at"], "2026-09-30T10:00:00+00:00")
        revisions = item["payload"]["revisions"]
        self.assertFalse(revisions[-1]["applied"])
        self.assertEqual(revisions[-1]["not_applied_reason"], "not_later_than_current")

        # 同时刻的重复观测也不能覆盖。
        before = item["payload"]["assessment"]["score"]
        item = self.service.act(item["id"], "report_revision", {
            "observed_at": "2026-09-30T10:00:00+00:00",
            "miss_distance_m": 5,
            "covariance_m": 10,
            "source": "tracking-station-2-dup",
        }, "analyst-1", "analyst", item["version"])
        self.assertEqual(item["payload"]["miss_distance_m"], 80)
        self.assertEqual(item["payload"]["assessment"]["score"], before)

    def test_later_revision_after_approval_invalidates_it(self):
        item = assess(self.service, make_item(self.service))
        item = self.service.act(item["id"], "record_opinion", {
            "operator": "Org-A", "opinion": "approve",
        }, "op-a", "operator", item["version"])
        item = self.service.act(item["id"], "record_opinion", {
            "operator": "Org-B", "opinion": "approve",
        }, "op-b", "operator", item["version"])
        item = self.service.act(item["id"], "approve", {
            "fuel_cost_m_s": 2.5,
            "maneuver_window": "2026-10-01T08:00:00Z/2026-10-01T09:00:00Z",
        }, "coord-1", "coordinator", item["version"])
        self.assertEqual(item["status"], "coordinating")
        self.assertIn("approved_maneuver", item["payload"])

        # 批准后出现更晚修订：批准失效，退回待复核。
        item = self.service.act(item["id"], "report_revision", {
            "observed_at": "2026-09-30T12:00:00+00:00",
            "miss_distance_m": 300,
            "covariance_m": 100,
            "source": "tracking-station-3",
        }, "analyst-1", "analyst", item["version"])
        self.assertEqual(item["status"], "assessed")
        self.assertNotIn("approved_maneuver", item["payload"])
        self.assertIn("approval_invalidated", item["payload"])
        # 本轮表态全部清空，必须重新协调。
        self.assertEqual(item["payload"]["opinions"], [])
        with self.assertRaises(DomainError) as context:
            self.service.act(item["id"], "approve", {
                "fuel_cost_m_s": 1,
                "maneuver_window": "w",
            }, "coord-1", "coordinator", item["version"])
        self.assertEqual(context.exception.code, "operators_not_unanimous")

        # 重新全员同意后可以再次批准。
        item = self.service.act(item["id"], "record_opinion", {
            "operator": "Org-A", "opinion": "approve",
        }, "op-a", "operator", item["version"])
        item = self.service.act(item["id"], "record_opinion", {
            "operator": "Org-B", "opinion": "approve",
        }, "op-b", "operator", item["version"])
        item = self.service.act(item["id"], "approve", {
            "fuel_cost_m_s": 2.5,
            "maneuver_window": "2026-10-01T09:00:00Z/2026-10-01T10:00:00Z",
        }, "coord-1", "coordinator", item["version"])
        self.assertEqual(item["status"], "coordinating")
        self.assertNotIn("approval_invalidated", item["payload"])

    def test_same_operator_latest_opinion_wins(self):
        item = assess(self.service, make_item(self.service, operators=("Org-A",)))
        item = self.service.act(item["id"], "record_opinion", {
            "operator": "Org-A", "opinion": "reject", "reason": "unsafe",
        }, "op-a", "operator", item["version"])
        self.assertTrue(item["payload"]["conflict"])
        with self.assertRaises(DomainError) as context:
            self.service.act(item["id"], "approve", {
                "fuel_cost_m_s": 1, "maneuver_window": "w",
            }, "coord-1", "coordinator", item["version"])
        self.assertEqual(context.exception.code, "unresolved_conflict")

        # 同一运营方改主意：最新意见生效，冲突解除。
        item = self.service.act(item["id"], "record_opinion", {
            "operator": "Org-A", "opinion": "approve", "reason": "re-evaluated",
        }, "op-a", "operator", item["version"])
        self.assertFalse(item["payload"]["conflict"])
        opinions = item["payload"]["opinions"]
        self.assertEqual(len(opinions), 1)
        self.assertEqual(opinions[0]["opinion"], "approve")
        item = self.service.act(item["id"], "approve", {
            "fuel_cost_m_s": 1, "maneuver_window": "w",
        }, "coord-1", "coordinator", item["version"])
        self.assertEqual(item["status"], "coordinating")

    def test_approval_requires_all_operators(self):
        item = assess(self.service, make_item(self.service))
        item = self.service.act(item["id"], "record_opinion", {
            "operator": "Org-A", "opinion": "approve",
        }, "op-a", "operator", item["version"])
        with self.assertRaises(DomainError) as context:
            self.service.act(item["id"], "approve", {
                "fuel_cost_m_s": 1, "maneuver_window": "w",
            }, "coord-1", "coordinator", item["version"])
        self.assertEqual(context.exception.code, "operators_not_unanimous")

    def test_operator_must_belong_to_event(self):
        item = assess(self.service, make_item(self.service))
        with self.assertRaises(DomainError) as context:
            self.service.act(item["id"], "record_opinion", {
                "operator": "Org-X", "opinion": "approve",
            }, "op-x", "operator", item["version"])
        self.assertEqual(context.exception.status, 403)


if __name__ == "__main__":
    unittest.main()
