import json
import tempfile
import unittest
from pathlib import Path

from cncap2027.engine import calculate, load_config
from cncap2027.vehicle_project import VehicleProject


EXAMPLES = Path(__file__).parent / "examples"


class VehicleProjectTests(unittest.TestCase):
    def test_opening_examples_preserves_values_and_ies_base(self):
        for source in EXAMPLES.glob("*.json"):
            with self.subTest(source=source):
                model = VehicleProject.from_file(source)
                config, base = model.build()
                self.assertEqual(config, load_config(source))
                self.assertEqual(base, EXAMPLES.resolve())
                config["project"] = "worker copy"
                self.assertNotEqual(model.config.get("project"), config["project"])

    def test_adb_mode_switch_keeps_inactive_inputs(self):
        source = next(EXAMPLES.glob("03-*.json"))
        model = VehicleProject.from_file(source)
        model.has_adb = False
        snapshot, _ = model.build()
        self.assertNotIn("adb_scenarios", snapshot["measurements"])
        self.assertNotIn("activation_oncoming_m", snapshot["evidence"])
        model.has_adb = True
        self.assertEqual(model.build()[0], load_config(source))

    def test_folder_assignment_and_release(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            folder = Path(temp_dir).resolve()
            for name in ("lbr.IES", "PosL.ies", "ADBR1.ies", "backup-LBL.ies"):
                (folder / name).touch()
            model = VehicleProject()
            model.load_folder(folder)
            self.assertEqual(model.lamp("LBR"), {"ies": str(folder / "lbr.IES")})
            self.assertEqual(model.lamp("ADBR1"), {"ies": str(folder / "ADBR1.ies")})
            self.assertEqual(model.unassigned, [folder / "backup-LBL.ies"])
            self.assertNotIn("PosL.ies", str(model.build()[0]))
            self.assertNotIn("adb_scenarios", model.build()[0]["photometry"])
            model.has_adb = True
            self.assertEqual(model.build()[0]["photometry"]["adb_scenarios"]["oncoming_50"]["right"],
                             model.lamp("ADBR1"))
            model.set_lamp("LBL", {"ies": str(model.unassigned[0]), "x_m": -1})
            self.assertEqual(model.unassigned, [])
            self.assertEqual(model.lamp("LBL")["x_m"], -1)
            model.set_lamp("LBL", {})
            self.assertEqual(model.unassigned, [folder / "backup-LBL.ies"])

    def test_invalid_project_is_not_coerced_on_load(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "project.json"
            for section in [
                {"photometry": {"low_beam": {"left": {"ies": 123}}}},
                {"photometry": {"low_beam": {"left": {"x_m": "0.75"}}}},
                {"measurements": {"low_beam": 3}},
                {"evidence": {"basic_glare_lm": []}},
            ]:
                path.write_text(json.dumps({"has_adb": False, **section}), encoding="utf-8")
                with self.subTest(section=section), self.assertRaises(ValueError):
                    VehicleProject.from_file(path)
            incompatible = {"has_adb": False, "evidence": {"adb_glare_lm": 0.1}}
            path.write_text(json.dumps(incompatible), encoding="utf-8")
            self.assertEqual(VehicleProject.from_file(path).build()[0], incompatible)

    def test_folder_uses_legacy_beam_side_and_line_markers(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder).resolve()
            expected = {"LBL": "sample_lB_l.IES", "HBR": "sampleHB_R.ies"}
            for index in range(1, 7):
                for side in "LR":
                    expected[f"ADB{side}{index}"] = f"sampleLine{index}_{side}_lab.ies"
            for filename in expected.values():
                (folder / filename).touch()
            for filename in ("sample_line6_L_R.ies", "sample_line10_L.ies", "HB.ies", "sample_L_LB.ies"):
                (folder / filename).touch()
            model = VehicleProject()
            model.load_folder(folder)
            for slot, filename in expected.items():
                self.assertEqual(model.lamp(slot), {"ies": str(folder / filename)})
            self.assertEqual({p.name for p in model.unassigned},
                             {"sample_line6_L_R.ies", "sample_line10_L.ies", "HB.ies", "sample_L_LB.ies"})

    def test_duplicate_does_not_win_by_directory_order(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder).resolve()
            for filename in ("A_LB_L.ies", "B_LB_L.ies", "LBL.ies", "LBR.ies"):
                (folder / filename).touch()
            model = VehicleProject()
            model.load_folder(folder)
            self.assertEqual(model.lamp("LBL"), {})
            self.assertEqual(model.lamp("LBR")["ies"], str(folder / "LBR.ies"))
            self.assertEqual(len(model.unassigned), 3)
            model.set_lamp("LBL", {"ies": str(folder / "B_LB_L.ies")})
            model.load_folder(folder)
            self.assertEqual(model.lamp("LBL")["ies"], str(folder / "B_LB_L.ies"))
            self.assertEqual({p.name for p in model.unassigned}, {"A_LB_L.ies", "LBL.ies"})

    def test_relabeling_adb_slots_keeps_lamps_together_and_measurements_fixed(self):
        project = VehicleProject.from_file(next(EXAMPLES.glob("04-*.json")))
        project.has_adb = True
        original_order = project.adb_order
        left, right = project.lamp("LBL"), project.lamp("LBR")
        project.set_lamp("ADBL4", left)
        project.set_lamp("ADBR4", right)
        measurements = {"adb_scenarios": {"oncoming_200": {"straight": 123}}}
        project.config["measurements"] = measurements
        with self.assertRaises(ValueError):
            project.set_adb_order(("oncoming_50",) * 6)
        self.assertEqual(project.adb_order, original_order)
        order = list(original_order)
        order[0], order[3] = order[3], order[0]
        project.set_adb_order(order)
        self.assertEqual(project.lamp("ADBL4"), left)
        self.assertEqual(project.lamp("ADBR4"), right)
        self.assertEqual(project.lamp("ADBL1"), {})
        report = calculate(*project.build())
        self.assertEqual(report["input_config"]["measurements"], measurements)
        scenes = report["input_config"]["photometry"]["adb_scenarios"]
        self.assertEqual(scenes, {"oncoming_50": {"left": left, "right": right}})
        self.assertIn("adb_glare_50m", report["measurement_details"])
        project.set_lamp("ADBL1", left)
        project.set_adb_order(original_order)
        self.assertEqual(project.config["photometry"]["adb_scenarios"],
                         {"oncoming_50": {"left": left}, "following_50": {"left": left, "right": right}})
