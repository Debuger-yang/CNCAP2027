import math
import unittest

from cncap2027.scoring import ADB_RULES, HIGH_RULES, LOW_RULES, SCENARIOS, evaluate


def item(result, ident):
    return next(row for row in result["items"] if row["id"] == ident)


class ScoringTests(unittest.TestCase):
    def test_published_example_and_missing_data(self):
        result = evaluate(False, {"straight": 100})
        self.assertEqual(item(result, "low_beam.straight")["score"], 0.837)
        self.assertEqual(item(result, "low_beam.straight")["regulation_score"], 0.84)
        self.assertEqual(result["known_subtotal"], 0.837)
        self.assertEqual(result["regulation_known_subtotal"], 0.84)
        self.assertIsNone(result["total"])
        self.assertIsNone(result["regulation_total"])
        self.assertIsNone(result["base_subtotals"]["low_beam"])
        self.assertIsNone(item(result, "auto_beam_approved")["score"])

    def test_full_vehicle_requires_every_applicable_item(self):
        low = {k: v.upper_m for k, v in LOW_RULES.items()}
        high = {k: v.upper_m for k, v in HIGH_RULES.items()}
        e = dict.fromkeys(("highway_mode_approved", "cornering_mode_approved", "auto_beam_approved",
                           "leveling_approved", "reverse_projection_approved", "turn_projection_approved",
                           "trajectory_test_passed"), True)
        e.update(position_lamp_area_cm2=60, basic_glare_lm=0.63)
        for side in ("left", "right"):
            e.update({f"cutoff_vertical_{side}_pct": 0.4, f"cutoff_horizontal_{side}_pct": -1.0,
                      f"cutoff_drift_{side}_mrad": -3, f"cutoff_sharpness_{side}": 0.48})
        result = evaluate(False, low, high, evidence=e)
        self.assertEqual(result["base_subtotals"], {"low_beam": 6.9, "high_beam": 2.5})
        self.assertEqual(result["known_subtotal"], 10.3)
        self.assertEqual(result["total"], 10)
        del e["cutoff_sharpness_left"]
        incomplete = evaluate(False, low, high, evidence=e)
        self.assertIsNone(incomplete["total"])
        self.assertEqual(incomplete["missing"], ["cutoff_sharpness_left"])

    def test_table4_and_5_boundary_inclusivity(self):
        for has_adb, area, flux, expected in [
                (False, 60, 0.26, -1), (False, 60, 0.260001, 0), (False, 60, 0.63, 0),
                (False, 60, 0.77, -0.5), (False, 60, 0.77001, -1), (False, 61, 0.72, 0),
                (False, 61, 0.83, -0.5), (True, 60, 0.26, -0.6), (True, 61, 0.73, -0.3)]:
            with self.subTest(has_adb=has_adb, area=area, flux=flux):
                res = evaluate(has_adb, evidence={"position_lamp_area_cm2": area, "basic_glare_lm": flux})
                self.assertEqual(item(res, "basic_glare")["score"], expected)
        for area, flux, expected in ((60, .30, 0), (60, .31, -.4), (61, .36, 0), (61, .37, -.4)):
            res = evaluate(True, evidence={"position_lamp_area_cm2": area, "adb_glare_lm": flux})
            self.assertEqual(item(res, "adb_glare")["score"], expected)

    def test_per_side_cutoff_thresholds_and_drift_sign(self):
        res = evaluate(False, evidence={
            "cutoff_vertical_left_pct": .41, "cutoff_horizontal_left_pct": -1.1,
            "cutoff_drift_left_mrad": 2, "cutoff_drift_right_mrad": -3.01,
            "cutoff_sharpness_right": .481,
        })
        self.assertEqual(item(res, "cutoff_initial_left")["score"], -.25)
        self.assertEqual(item(res, "cutoff_drift_left")["score"], 0)
        self.assertEqual(item(res, "cutoff_drift_right")["score"], -.1)
        self.assertEqual(item(res, "cutoff_sharpness_right")["score"], -.25)

    def test_adb_requires_all_six_and_averages_before_nonlinear_scoring(self):
        # Five short reaches and one long reach produce the published mean of 100 m.
        cases = {case: {"straight": (350 if i == 5 else 50)} for i, case in enumerate(SCENARIOS)}
        result = evaluate(True, {"straight": 100}, adb_scenarios=cases)
        self.assertEqual(item(result, "adb.straight")["value_m"], 100)
        self.assertEqual(item(result, "adb.straight")["score"], .502)
        self.assertEqual(item(result, "adb.straight")["regulation_score"], .50)
        self.assertEqual(item(result, "low_beam.straight")["score"], .335)
        # The original value rounds to .33 directly; rounding .335 again would give .34.
        self.assertEqual(item(result, "low_beam.straight")["regulation_score"], .33)
        del cases["following_200"]
        missing = item(evaluate(True, adb_scenarios=cases), "adb.straight")
        self.assertIsNone(missing["score"])
        self.assertEqual(missing["missing_scenarios"], ["following_200"])
        all_max = {case: {k: v.upper_m for k, v in ADB_RULES.items()} for case in SCENARIOS}
        self.assertEqual(evaluate(True, adb_scenarios=all_max)["base_subtotals"]["adb"], 5.04)

    def test_road_test_preconditions_and_fractional_points(self):
        e = {"activation_oncoming_m": 400, "activation_following_m": 100,
             "response_oncoming_s": 1.5, "response_following_s": 1.499,
             "single_mask_lanes": 1, "multi_mask_passed": True, "reflector_masked": False}
        result = evaluate(True, evidence=e)
        self.assertEqual(result["known_subtotal"], .975)
        self.assertEqual(item(result, "reflector")["score"], .2)
        self.assertEqual(item(result, "single_mask")["score"], .125)
        self.assertEqual(item(result, "single_mask")["regulation_score"], .125)
        self.assertEqual(item(result, "response_oncoming")["score"], -.1)
        self.assertEqual(item(evaluate(True, evidence=dict(e, single_mask_lanes=2)), "single_mask")["score"], 0)
        no_trigger = evaluate(True, evidence=dict(e, activation_following_m="no_activation"))
        self.assertEqual(item(no_trigger, "activation_following")["score"], 0)
        self.assertIsNone(item(no_trigger, "reflector")["score"])
        self.assertIsNone(item(no_trigger, "single_mask")["score"])
        self.assertIsNone(item(no_trigger, "response_following")["score"])
        late = evaluate(True, evidence=dict(e, activation_oncoming_m=399, activation_following_m=99))
        self.assertEqual(item(late, "activation_oncoming")["score"], 0)
        self.assertIsNone(item(late, "response_oncoming")["score"])
        self.assertEqual(item(late, "activation_following")["score"], 0)
        self.assertIsNone(item(late, "single_mask")["score"])

    def test_invalid_input_does_not_become_missing_or_a_score(self):
        for invalid in (-1, "100", False, math.nan, math.inf):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                evaluate(False, {"straight": invalid})
        with self.assertRaises(ValueError):
            evaluate(False, {"straigth": 100})
        with self.assertRaises(ValueError):
            evaluate(False, evidence={"highway_mode_approved": 1})
        with self.assertRaises(ValueError):
            evaluate(False, adb_scenarios={"oncoming_50": {"straight": 100}})
        with self.assertRaises(ValueError):
            evaluate(True, evidence={"response_following_s": -2})


if __name__ == "__main__":
    unittest.main()
