import json
import math
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
import xml.etree.ElementTree as ET
from zipfile import ZipFile

from cncap2027.engine import calculate, load_lamp_pair
from cncap2027.glare import weighted_flux
from cncap2027.output import display_score, write_report
from cncap2027.scoring import SCENARIOS


class ApplicationTests(unittest.TestCase):
    def demo_lamps(self):
        # In the Windows distribution, examples sit beside the cncap2027 package.
        package = Path(__file__).resolve().parent
        folder = package / "examples" if (package / "examples").is_dir() else package.parent / "examples"
        return {side: {"ies": str(folder / "synthetic-TypeA.ies"), "x_m": x, "y_m": 0, "z_m": .75}
                for side, x in (("left", -.75), ("right", .75))}

    def test_low_beam_and_measured_only_progress(self):
        messages = []
        config = {"has_adb": False, "photometry": {"low_beam": self.demo_lamps(), "projection": "radial"}}
        result = calculate(config, progress=messages.append)
        self.assertEqual(messages, ["正在测量：基础近光", "正在评分：C-NCAP 2027 附录 S"])
        # From the 260114 calculator's unrounded Excel cells for this same candela matrix.
        native_distances = (127.4, 121.711111111111, 105.637777777778, 24.1, 40.333333333333)
        for item, expected in zip(result["items"][:5], native_distances):
            with self.subTest(item=item["id"]):
                self.assertAlmostEqual(item["value_m"], expected, places=8)
        self.assertEqual(result["base_subtotals"]["low_beam"], 6.709)
        self.assertEqual(result["regulation_base_subtotals"]["low_beam"], 6.71)
        self.assertAlmostEqual(result["measurement_details"]["basic_glare"]["flux_lm"], 36.477293442475265)
        messages.clear()
        result = calculate({"has_adb": False, "measurements": {"low_beam": {"straight": 100}}},
                           progress=messages.append)
        self.assertEqual(messages, ["正在评分：C-NCAP 2027 附录 S"])
        self.assertEqual(result["items"][0]["score"], .837)
        self.assertEqual(result["items"][0]["regulation_score"], .84)

    def test_optical_tasks_have_stable_order_and_share_lamps_with_glare(self):
        pair = self.demo_lamps()
        config = {"has_adb": True, "photometry": {
            "adb_scenarios": dict.fromkeys(reversed(SCENARIOS), pair),
            "high_beam": pair, "low_beam": pair, "projection": "radial", "step_m": 1,
        }}
        messages = []
        with patch("cncap2027.engine.load_lamp_pair", wraps=load_lamp_pair) as load:
            result = calculate(config, progress=messages.append)
        groups = ["low_beam", "high_beam", *SCENARIOS]
        self.assertEqual([call.args[1] for call in load.call_args_list], groups)
        self.assertEqual(list(result["inputs"]), groups)
        labels = ["基础近光", "远光", *(f"ADB {kind} {m} m" for kind in ("对向", "跟车") for m in (50, 100, 200))]
        self.assertEqual(messages, [*(f"正在测量：{label}" for label in labels), "正在评分：C-NCAP 2027 附录 S"])
        self.assertEqual(result["base_subtotals"], {"low_beam": 2.684, "high_beam": 1.011, "adb": 4.292})
        self.assertEqual(result["regulation_base_subtotals"], {"low_beam": 2.68, "high_beam": 1.01, "adb": 4.3})
        detail = result["measurement_details"]
        native_high_beam = (109.642676031146, 93.531566428036, 105.181829318306,
                            93.801744652637, 108.705828124383)
        for index, expected in enumerate(native_high_beam, 1):
            with self.subTest(point=index):
                self.assertAlmostEqual(detail["high_beam"][f"point{index}"]["value_m"], expected, places=8)
        self.assertAlmostEqual(detail["oncoming_50"]["pedestrian_1_5m"]["value_m"], 126.14998779015244)
        self.assertAlmostEqual(detail["adb_glare_50m"]["flux_lm"], 14.822856098778967)

    def test_conflict_in_last_scene_is_reported_before_any_photometry(self):
        pair = self.demo_lamps()
        config = {"has_adb": True, "photometry": {"projection": "radial", "low_beam": pair,
                  "adb_scenarios": {"following_200": pair}},
                  "measurements": {"adb_scenarios": {"following_200": {"straight": 0}}}}
        load = Mock(side_effect=AssertionError("Unexpected grid load"))
        progress = Mock()
        with self.assertRaisesRegex(ValueError, "following_200.straight 同时提供"):
            calculate(config, load_grid=load, progress=progress)
        load.assert_not_called()
        progress.assert_not_called()

    def test_high_beam_uses_left_distance_and_each_lamps_illuminance(self):
        # Different optical centres in all three axes, and the right lamp listed first.
        pair = {"right": {"ies": "right.ies", "x_m": .9, "y_m": -1, "z_m": .7},
                "left": {"ies": "left.ies", "x_m": -.65, "y_m": .3, "z_m": .8}}
        sources = {"left": 3400, "right": 5900}

        def load(path):
            grid = Mock()
            grid.candela.return_value = sources[path.stem]
            grid.metadata.return_value = {}
            return grid

        result = calculate({"has_adb": False, "photometry": {"high_beam": pair}}, load_grid=load)
        points = result["measurement_details"]["high_beam"]
        for row in points.values():
            sample = row["samples"][0]
            point = sample["position_m"]
            left_r = math.dist(point, (-.65, .3, .8))
            right_r = math.dist(point, (.9, -1, .7))
            expected_cd = 3400 + 5900 * (left_r / right_r)**2
            self.assertAlmostEqual(sample["intensity_cd"], expected_cd)
            self.assertAlmostEqual(row["value_m"], math.sqrt(expected_cd / 3))

    def test_measured_width_preserves_ies_result_and_other_scores(self):
        for projection, half_width, estimate_available in (("radial", 2, True), ("vertical", 80, True),
                                                         ("vertical", 2, False)):
            with self.subTest(projection=projection, half_width=half_width):
                config = {"has_adb": False, "photometry": {
                    "low_beam": self.demo_lamps(), "projection": projection, "half_width_m": half_width}}
                config["measurements"] = {"low_beam": {"intersection_width": None}}
                automatic = calculate(config)
                config["measurements"]["low_beam"]["intersection_width"] = 0
                result = calculate(config)
                estimate = automatic["measurement_details"]["low_beam"]["intersection_width"]
                self.assertEqual(estimate["value_m"] is not None, estimate_available)
                detail = result["measurement_details"]["low_beam"]["intersection_width"]
                self.assertEqual(detail["ies_estimate"], estimate)
                self.assertEqual(detail["value_m"], 0)
                for row, previous in zip(result["items"], automatic["items"]):
                    if row["id"] == "low_beam.intersection_width":
                        self.assertEqual((row["value_m"], row["value_source"], row["score"]), (0, "measured", 0))
                        self.assertEqual(previous.get("value_source"), "ies" if estimate_available else None)
                    else:
                        self.assertEqual(row, previous)

    def test_manual_width_validation_does_not_relax_other_conflicts(self):
        pair = self.demo_lamps()
        for group, metric, value, reason in (("low_beam", "curve_width", -1, "超出数值范围"),
                                             ("low_beam", "straight", 1, "同时提供"),
                                             ("high_beam", "point1", 1, "同时提供")):
            load = Mock()
            with self.subTest(group=group, metric=metric), self.assertRaisesRegex(ValueError, reason):
                calculate({"has_adb": False, "photometry": {"projection": "radial", group: pair},
                           "measurements": {group: {metric: value}}}, load_grid=load)
            load.assert_not_called()

    def test_glare_with_flat_screen(self):
        def detector(x, y, z):
            self.assertEqual(y, 50.)
            self.assertTrue(.93 < z < 1.62)
            return 1.
        # Fig. S.11a: 6 white cells, 14 yellow and 20 orange.
        expected = (6 + 14 * .5 + 20 * .2) * 9.2 * .69 / 40
        self.assertAlmostEqual(weighted_flux(detector)["flux_lm"], expected)
        expected_adb = (.2 + .5 + 1 + 1 + .5) * 2.45 * .69 / 5
        self.assertAlmostEqual(weighted_flux(detector, adb=True)["flux_lm"], expected_adb)

    def test_cli_report_export(self):
        r = calculate({"has_adb": False, "project": "算例 & 复核",
                       "measurements": {"low_beam": {"straight": 100}}})
        self.assertEqual(r["items"][0]["score"], .837)
        self.assertEqual(r["items"][0]["regulation_score"], .84)
        self.assertIsNone(r["total"])
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as folder:
            path = Path(folder)
            write_report(r, path / "结果.json")
            self.assertEqual(json.loads((path / "结果.json").read_text(encoding="utf8")), r)
            write_report(r, path / "结果.xlsx")
            with ZipFile(path / "结果.xlsx") as book:
                self.assertIsNone(book.testzip())
                sheet = ET.fromstring(book.read("xl/worksheets/sheet1.xml"))
                rows = {row.findtext("{*}c[2]/{*}is/{*}t"): row for row in sheet.findall(".//{*}row")}
                def cell(label, column):
                    row = rows[label]
                    return row.find(f"{{*}}c[@r='{column}{row.get('r')}']")
                for label in ("直道引导距离", "已评项小计"):
                    self.assertEqual(cell(label, "D").findtext("{*}v"), "0.837")
                    self.assertEqual(cell(label, "E").findtext("{*}v"), "0.84")
                self.assertIsNone(cell("整车得分", "D"))
                self.assertIsNone(cell("整车得分", "E"))
                styles = ET.fromstring(book.read("xl/styles.xml"))
                xfs = styles.find("{*}cellXfs")
                fmts = {f.get("numFmtId"): f.get("formatCode") for f in styles.find("{*}numFmts")}
                for col, fmt in [("D", "0.000"), ("E", "0.00#")]:
                    style = int(cell("直道引导距离", col).get("s"))
                    self.assertEqual(fmts[xfs[style].get("numFmtId")], fmt)

    def test_score_display_preserves_fixed_increments_and_missing_results(self):
        for value, ppt, regulation in ((.84, "0.840", "0.84"), (.125, "0.125", "0.125"),
                                       (-.25, "-0.250", "-0.25"), (0, "0.000", "0.00"),
                                       (None, "未评定", "未评定")):
            with self.subTest(value=value):
                self.assertEqual(display_score(value), ppt)
                self.assertEqual(display_score(value, regulation=True), regulation)

    def test_unknown_configuration_rejected(self):
        for c in [{"has_adb": "false"}, {"has_adb": False, "unknown": 1},
                  {"has_adb": False, "photometry": {"low_beam": {}}},
                  {"has_adb": True, "measurements": {"low_beam": {"wrong": 1}}}]:
            with self.subTest(c=c), self.assertRaises(ValueError):
                calculate(c)
