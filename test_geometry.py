"""Analytic fields test the coordinates and isolux interpolation without an IES."""

import math
import unittest

from cncap2027.geometry import adb_scene, high_beam, low_beam, low_beam_260114


class GeometryTests(unittest.TestCase):
    def test_260114_three_pass_distance_and_curve_origins(self):
        slope = math.tan(math.radians(5))

        def field(x, y, z):
            return max(0, 61.055 + 20*(x - y*slope) - y)

        samples = low_beam_260114(field)["curve"]["samples"]
        one_lux = [s for s in samples if s["threshold_lx"] == 1]
        for sample, expected in zip(one_lux, (60.0, 94.9, 129.7)):
            self.assertAlmostEqual(sample["value_m"], expected)
        passes = one_lux[1]["passes"]
        self.assertEqual([p["step_m"] for p in passes], [15.0, 1.0, 0.1])
        self.assertEqual([p["start_m"] for p in passes], [50.0, 65.0, 93.0])

    def test_260114_dark_beam_raw_width_and_bright_beam_scan_limits(self):
        # The 260114 calculator returns 17.9 m longitudinally and -34.2 m
        # transversely for an entirely dark field. Only physical widths can be scored.
        dark = low_beam_260114(lambda x, y, z: 0)
        for key in ("straight", "curve", "pedestrian"):
            self.assertAlmostEqual(dark[key]["value_m"], 17.9)
            self.assertTrue(dark[key]["samples"][0]["passes"][-1]["first_sample_below_threshold"])
        for key in ("intersection_width", "curve_width"):
            self.assertEqual(dark[key]["status"], "not_evaluated")
            self.assertAlmostEqual(dark[key]["samples"][0]["raw_width_m"], -34.2)
        bright = low_beam_260114(lambda x, y, z: 5.0)
        for key, expected in zip(bright, (233.30, 269.22, 126.48, 500, 500)):
            self.assertAlmostEqual(bright[key]["value_m"], expected)

    def test_260114_width_scans_from_each_task_origin_and_traces_missing_readings(self):
        def field(x, y, z):
            if y == 20:
                return None
            return 3.0 if 0 <= x <= 13.35 else 0

        rows = low_beam_260114(field)
        for sample in rows["curve_width"]["samples"]:
            self.assertAlmostEqual(sample["value_m"], 13.3)
            self.assertAlmostEqual(sample["edges_m"][0], 0.0)
            self.assertAlmostEqual(sample["edges_m"][1], 13.3)
            self.assertEqual(sample["x_origin_m"], 5)
        self.assertIsNone(rows["intersection_width"]["value_m"])
        missing = rows["intersection_width"]["samples"][1]
        self.assertEqual(missing["right_ray"]["missing_at_m"], -5)

    def test_all_nine_distances_and_perpendicular_curve_offsets(self):
        def field(x, y, z):
            return max(0, 10 + 0.2*x + z - y/8)

        result = low_beam(field, step_m=1.0, max_distance_m=160.0, half_width_m=60.0)
        self.assertEqual(len(result["straight"]["samples"]), 9)
        self.assertAlmostEqual(result["straight"]["value_m"], (10 + .2*1.75 - 3)*8)
        self.assertAlmostEqual(result["pedestrian"]["value_m"], (10 - .2*5.25 + .25 - 3)*8)
        a = math.radians(5)
        expected = (10 + .2*1.75/math.cos(a) - 3) / (.125 - .2*math.tan(a))
        self.assertAlmostEqual(result["curve"]["value_m"], expected)

    def test_width_lines_and_interpolation_with_partial_angular_coverage(self):
        def field(x, y, z):
            if abs(x) > 25:
                return None
            return max(0, 10.125 + y/10 + 2*z - abs(x))

        result = low_beam(field, step_m=1.0)
        self.assertAlmostEqual(result["intersection_width"]["value_m"], (7.125 + 1.5 + .5)*2)
        self.assertAlmostEqual(result["curve_width"]["value_m"], (7.125 + 4)*2)
        self.assertTrue(result["curve_width"]["samples"][0]["missing_before"])
        self.assertTrue(result["curve_width"]["samples"][0]["missing_after"])

    def test_disconnected_bright_regions_and_interior_gaps(self):
        def islands(x, y, z):
            return max(0, 5-abs(abs(x)-12))

        self.assertIsNone(low_beam(islands, step_m=1)["intersection_width"]["value_m"])

        def gap(x, y, z):
            return None if x == 2 else max(0, 20-abs(x))

        self.assertIsNone(low_beam(gap, step_m=1)["curve_width"]["value_m"])

    def test_clipped_range_missing_crossing_and_no_false_zero(self):
        def field(x, y, z):
            return None if y < 10 else max(0, 130.5 - y)

        self.assertIsNone(low_beam(field, max_distance_m=100, step_m=1)["straight"]["value_m"])
        complete = low_beam(field, max_distance_m=200, step_m=1)["straight"]
        self.assertAlmostEqual(complete["value_m"], 127.5)
        self.assertTrue(complete["samples"][0]["missing_before"])
        self.assertIsNone(low_beam(lambda *coords: 2.5, step_m=1)["straight"]["value_m"])

    def test_adb_elevated_layer_and_high_beam_landmarks(self):
        scene = adb_scene(lambda x, y, z: max(0, 50+z-y), step_m=1)
        self.assertEqual(len(scene), 6)
        self.assertAlmostEqual(scene["pedestrian"]["value_m"], 47.25)
        self.assertAlmostEqual(scene["pedestrian_1_5m"]["value_m"], 48.5)
        points = []

        def cd(x, y, z):
            points.append((x, y, z))
            return 30000 if z >= 0 else None

        hb = high_beam(cd)
        self.assertEqual(points, [(0,100,.75), (-20,100,.75), (0,100,9.75),
                                  (20,100,.75), (0,100,-1.25)])
        self.assertEqual(hb["point1"]["value_m"], 100.0)
        self.assertIsNone(hb["point5"]["value_m"])

    def test_midpoint_reveals_additional_longitudinal_crossings(self):
        def field(x, y, z):
            return max(0, 20-y, 10-100*abs(y-25.5))

        for rows in (low_beam(field, step_m=1), adb_scene(field, step_m=1)):
            item = rows["straight"]
            self.assertIsNone(item["value_m"])
            sample = item["samples"][0]
            self.assertIn("工程容差", sample["reason"])
            check = sample["refinement"]
            self.assertEqual((check["coarse_crossings"], check["fine_crossings"]), (1, 3))
            self.assertEqual(check["coarse_value_m"], 19)
            self.assertGreater(check["fine_value_m"], 25)

    def test_hidden_near_island_does_not_move_farthest_distance(self):
        def field(x, y, z):
            return max(0, 10-abs(y-20), 8-100*abs(y-5.5))

        for rows in (low_beam(field, step_m=1), adb_scene(field, step_m=1)):
            sample = rows["straight"]["samples"][0]  # 1 lx
            self.assertEqual(sample["value_m"], 29)
            check = sample["refinement"]
            self.assertEqual((check["coarse_crossings"], check["fine_crossings"]), (2, 4))
            self.assertEqual(check["abs_delta_m"], 0)
            self.assertIn("仅最远下降交点", check["note"])

    def test_hidden_gap_splits_only_the_centerline_interval(self):
        def field(x, y, z):
            return 0 if abs(x-0.5) < 0.1 else max(0, 6-abs(x))

        sample = low_beam(field, step_m=1)["curve_width"]["samples"][0]
        self.assertAlmostEqual(sample["value_m"], 3.4, delta=0.005)
        self.assertEqual(len(sample["candidate_intervals_m"]), 2)
        self.assertEqual(sample["edges_m"], sample["candidate_intervals_m"][0])
        ref = sample["refinement"]
        self.assertLessEqual(ref["fine_step_m"], 0.005)
        self.assertEqual(ref["history"][0]["crossings"], 4)

    def test_two_apparently_stable_widths_can_hide_a_thin_dark_gap(self):
        def field(x, y, z):
            return 0 if abs(x - 1.015625) < 0.008 else max(0, 6 - abs(x))

        sample = low_beam(field, step_m=.25, half_width_m=12)["intersection_width"]["samples"][0]
        history = sample["refinement"]["history"]
        self.assertEqual(history[0]["value_m"], history[1]["value_m"])
        self.assertAlmostEqual(sample["value_m"], 4.007625, delta=0.005)
        self.assertAlmostEqual(sample["edges_m"][0], -3)

    def test_unique_off_center_interval_needs_full_coverage(self):
        def isolated(x, y, z):
            return max(0, 5 - abs(x - 8))

        def clipped(x, y, z):
            return None if x < -10 else isolated(x, y, z)

        width = low_beam(isolated, step_m=.5, half_width_m=12)["curve_width"]["samples"][0]
        self.assertEqual(width["edges_m"], [6, 10])
        self.assertIsNone(low_beam(clipped, step_m=.5, half_width_m=12)["curve_width"]["value_m"])

    def test_width_clipped_at_scan_limit_stays_unresolved(self):
        result = low_beam(lambda x, y, z: max(0, 6 - abs(x)), half_width_m=2)
        sample = result["intersection_width"]["samples"][0]
        self.assertIsNone(sample["value_m"])
        self.assertIn("未包围", sample["reason"])

    def test_clipped_outlying_island_does_not_hide_the_closed_center_interval(self):
        def field(x, y, z):
            return max(0, 6 - abs(x), 7 - abs(x + 10))

        sample = low_beam(field, half_width_m=10)["curve_width"]["samples"][0]
        self.assertEqual(sample["candidate_intervals_m"], [[None, -6], [-3, 3]])
        self.assertEqual(sample["edges_m"], [-3, 3])
        self.assertEqual(sample["value_m"], 6)

    def test_same_topology_but_sensitive_crossing_remains_unscored(self):
        def field(x, y, z):
            return 6 if y < 5.4 else 0

        sample = low_beam(field, step_m=1)["straight"]["samples"][1]  # 3 lx
        self.assertIsNone(sample["value_m"])
        ref = sample["refinement"]
        self.assertEqual((ref["coarse_step_m"], ref["fine_step_m"]), (1, .5))
        self.assertEqual(ref["abs_delta_m"], .25)
        self.assertGreater(ref["abs_delta_m"], ref["engineering_tolerance_m"])


if __name__ == "__main__":
    unittest.main()
