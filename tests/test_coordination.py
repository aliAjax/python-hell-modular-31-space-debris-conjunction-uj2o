import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from src.repository import Repository
from src.service import Service
from src.domain import ConflictError, DomainError


class CoordinationTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.repo = Repository(self.tmp.name)
        self.repo.initialize()
        self.service = Service(self.repo)

    def tearDown(self):
        os.unlink(self.tmp.name)

    def _create(self):
        return self.service.create_item({
            "primary_object_id": "SAT-1",
            "secondary_object_id": "DEB-9",
            "tca": "2026-09-30T12:00:00+00:00",
            "miss_distance_m": 120,
            "covariance_m": 100,
            "fuel_budget_m_s": 5,
            "track_age_hours": 1,
            "operating_organizations": ["Org-A", "Org-B"],
        }, "analyst-1", "analyst")

    def _revise(self, item, observed_at, distance, covariance=100, source="sensor-x"):
        return self.service.act(item["id"], "report_revision", {
            "observed_at": observed_at,
            "miss_distance_m": distance,
            "covariance_m": covariance,
            "source": source,
        }, "analyst-1", "analyst", item["version"])

    def test_late_observation_is_recorded_but_does_not_override(self):
        item = self._create()
        item = self._revise(item, "2026-09-29T10:00:00+00:00", 40)
        self.assertEqual(item["payload"]["miss_distance_m"], 40)
        self.assertEqual(item["payload"]["latest_observed_at"], "2026-09-29T10:00:00+00:00")

        # 晚到的旧观测：只能留在来源记录里，不能覆盖当前距离和风险
        late = self._revise(item, "2026-09-29T08:00:00+00:00", 9)
        self.assertEqual(late["status"], "pending")
        self.assertEqual(late["payload"]["miss_distance_m"], 40)
        revisions = late["payload"]["revisions"]
        self.assertEqual(len(revisions), 2)
        self.assertTrue(revisions[0]["applied"])
        self.assertFalse(revisions[1]["applied"])
        self.assertEqual(revisions[1]["note"], "late_observation")
        # 当前风险仍基于 40m 计算，而非晚到的 9m（9m/100m 比值 0.09）
        self.assertEqual(late["payload"]["assessment"]["distance_to_covariance_ratio"], 0.4)
        self.assertEqual(late["payload"]["assessment"]["score"], 92.0)

        # 同一观测时间的修订拒绝重复提交
        with self.assertRaises(DomainError) as context:
            self._revise(late, "2026-09-29T08:00:00+00:00", 5)
        self.assertEqual(context.exception.code, "duplicate_observation")

    def test_approval_invalidated_by_later_revision(self):
        item = self._create()
        item = self.service.act(item["id"], "assess", {"hours_to_tca": 18}, "a", "analyst", item["version"])
        item = self._revise(item, "2026-09-29T09:00:00+00:00", 30)
        self.assertEqual(item["status"], "assessed")
        for org in ("Org-A", "Org-B"):
            item = self.service.act(item["id"], "record_opinion", {
                "operator": org, "opinion": "approve",
            }, "operator-1", "operator", item["version"])
        item = self.service.act(item["id"], "approve", {
            "fuel_cost_m_s": 2.0, "maneuver_window": "w1",
        }, "coordinator-1", "coordinator", item["version"])
        self.assertEqual(item["status"], "coordinating")
        self.assertIn("approved_maneuver", item["payload"])

        # 批准后出现更晚的修订：原批准失效，退回待复核
        item = self._revise(item, "2026-09-29T11:00:00+00:00", 300)
        self.assertEqual(item["status"], "assessed")
        self.assertNotIn("approved_maneuver", item["payload"])
        self.assertEqual(item["payload"]["miss_distance_m"], 300)
        self.assertEqual(item["payload"]["invalidation"]["superseded_by"], "2026-09-29T11:00:00+00:00")
        # 旧意见同步失效，必须重新协调
        self.assertEqual(item["payload"]["opinions"], [])
        with self.assertRaises(DomainError) as context:
            self.service.act(item["id"], "approve", {
                "fuel_cost_m_s": 2.0, "maneuver_window": "w2",
            }, "coordinator-1", "coordinator", item["version"])
        self.assertEqual(context.exception.code, "operator_consent_required")

    def test_latest_operator_opinion_wins_and_all_must_agree(self):
        item = self._create()
        item = self.service.act(item["id"], "assess", {"hours_to_tca": 18}, "a", "analyst", item["version"])
        item = self.service.act(item["id"], "record_opinion", {
            "operator": "Org-A", "opinion": "reject", "reason": "不安全",
        }, "operator-1", "operator", item["version"])
        # 只有一个运营方同意，不能批准
        item = self.service.act(item["id"], "record_opinion", {
            "operator": "Org-B", "opinion": "approve",
        }, "operator-1", "operator", item["version"])
        with self.assertRaises(DomainError) as context:
            self.service.act(item["id"], "approve", {
                "fuel_cost_m_s": 1, "maneuver_window": "w",
            }, "coordinator-1", "coordinator", item["version"])
        self.assertEqual(context.exception.code, "unresolved_conflict")

        # 同一运营方再次表态，以最新意见为准；冲突解除
        item = self.service.act(item["id"], "record_opinion", {
            "operator": "Org-A", "opinion": "approve", "reason": "复算后安全",
        }, "operator-1", "operator", item["version"])
        self.assertFalse(item["payload"]["conflict"])
        self.assertEqual(len(item["payload"]["opinions"]), 3)
        self.assertEqual(item["payload"]["opinions"][-1]["supersedes"], "reject")
        item = self.service.act(item["id"], "approve", {
            "fuel_cost_m_s": 1, "maneuver_window": "w",
        }, "coordinator-1", "coordinator", item["version"])
        self.assertEqual(item["status"], "coordinating")

    def test_unknown_operator_cannot_record_opinion(self):
        item = self._create()
        item = self.service.act(item["id"], "assess", {"hours_to_tca": 18}, "a", "analyst", item["version"])
        with self.assertRaises(DomainError) as context:
            self.service.act(item["id"], "record_opinion", {
                "operator": "Org-X", "opinion": "approve",
            }, "operator-1", "operator", item["version"])
        self.assertEqual(context.exception.code, "unknown_operator")

    def test_event_requires_operator(self):
        with self.assertRaises(DomainError) as context:
            self.service.create_item({
                "primary_object_id": "SAT-1",
                "secondary_object_id": "DEB-9",
                "tca": "2026-09-30T12:00:00+00:00",
                "miss_distance_m": 120,
                "covariance_m": 100,
                "fuel_budget_m_s": 5,
                "track_age_hours": 1,
                "operating_organizations": [],
            }, "analyst-1", "analyst")
        self.assertEqual(context.exception.code, "invalid_operators")


if __name__ == "__main__":
    unittest.main()
