"""Meta-label operational rollout tests."""

from __future__ import annotations

import unittest

from app.services.bots.meta_label_operational import (
    build_operational_patch,
    operational_status,
)


class OperationalPatchTests(unittest.TestCase):
    def test_shadow_patch(self):
        patch = build_operational_patch("shadow")
        self.assertTrue(patch["calibration_gate_enabled"])
        self.assertTrue(patch["meta_label_shadow_mode"])
        self.assertEqual(patch["meta_label_model_mode"], "hybrid")

    def test_promote_requires_positive_oos(self):
        with self.assertRaises(ValueError):
            build_operational_patch("promote", walk_forward={"ok": False})

        with self.assertRaises(ValueError):
            build_operational_patch("promote", walk_forward={
                "ok": True,
                "aggregate": {"gbm_vs_baseline_avg": {"total_pnl": -1, "expectancy": -0.5}},
            })

    def test_promote_with_good_oos(self):
        patch = build_operational_patch("promote", walk_forward={
            "ok": True,
            "aggregate": {"gbm_vs_baseline_avg": {"total_pnl": 10, "expectancy": 1}},
        })
        self.assertTrue(patch["calibration_gate_enabled"])
        self.assertFalse(patch["meta_label_shadow_mode"])

    def test_promote_override(self):
        patch = build_operational_patch(
            "promote",
            walk_forward={"ok": False},
            require_positive_oos=False,
        )
        self.assertFalse(patch["meta_label_shadow_mode"])

    def test_rollback(self):
        patch = build_operational_patch("rollback")
        self.assertFalse(patch["calibration_gate_enabled"])
        self.assertEqual(patch["meta_label_model_mode"], "wilson")


class OperationalStatusTests(unittest.TestCase):
    def test_stage_detection(self):
        self.assertEqual(operational_status({})["stage"], "off")
        self.assertEqual(
            operational_status({"calibration_gate_enabled": True, "meta_label_shadow_mode": True})["stage"],
            "shadow",
        )
        self.assertEqual(
            operational_status({
                "calibration_gate_enabled": True,
                "meta_label_model_mode": "hybrid",
                "meta_label_shadow_mode": False,
            })["stage"],
            "live",
        )


if __name__ == "__main__":
    unittest.main()
