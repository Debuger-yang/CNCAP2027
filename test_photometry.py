import math
from pathlib import Path
import tempfile
import unittest

from cncap2027.photometry import IESGrid, Lamp, LampGroup, OutOfDomain


class PhotometryTests(unittest.TestCase):
    def fixture(self, text=None):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        path = Path(folder.name) / "lamp.ies"
        # Seven emitters assembled in a lamp; multiplier 2. The distribution
        # is deliberately brighter to the right and above the lamp axis.
        default = """IESNA:LM-63-2002
TILT=NONE
7 344 2 3 3 3 2 0 0 0
1 1 0
-50 0 50
-80 0 80
10 20 30
30 40 50
50 60 70
"""
        path.write_text(default if text is None else text(default), encoding="ascii")
        return IESGrid.load(path)

    def test_bilinear_candela_nonuniform_scales(self):
        grid = self.fixture()
        self.assertEqual(grid.candela(40, 25), 110)
        self.assertEqual(grid.candela(-80, -50), 20)
        self.assertEqual(grid.candela(80, 50), 140)
        with self.assertRaises(OutOfDomain):
            grid.candela(81, 0)

    def test_lamp_position_orientation_and_50m_plane(self):
        grid = self.fixture()
        lamp = Lamp(grid, -0.8, 0, 0.75)
        group = LampGroup([lamp])
        self.assertAlmostEqual(group.vertical_lux(-0.8, 50, 0.75), 80 / 2500)
        # Horizontal and vertical angles deliberately nonzero and independent.
        h, v = map(math.radians, (40, 25))
        r = 50
        x = -0.8 + r * math.sin(h) * math.cos(v)
        y = r * math.cos(h) * math.cos(v)
        z = 0.75 + r * math.sin(v)
        self.assertAlmostEqual(group.intensity(x, y, z), 110)
        self.assertAlmostEqual(group.radial_lux(x, y, z), 110 / 2500)
        self.assertAlmostEqual(group.vertical_lux(x, y, z), 110 / 2500 * y / r)
        self.assertEqual(group.horizontal_lux(x, y, z), 0.0)
        # Additional mounting yaw and pitch move the direction of the axis.
        aimed = LampGroup([Lamp(grid, -0.8, 0, 0.75, yaw_deg=40, pitch_deg=25)])
        self.assertAlmostEqual(aimed.intensity(x, y, z), 80)

    def test_two_distinct_lamps_sum_cd_and_lux(self):
        grid = self.fixture()
        lamps = [Lamp(grid, -0.8, 0, 0.8), Lamp(grid, 0.7, -0.1, 0.7)]
        group = LampGroup(lamps)
        cd_terms, lux_terms = [], []
        for lamp in lamps:
            dx, dy, dz = 2 - lamp.x_m, 20 - lamp.y_m, -lamp.z_m
            r = math.sqrt(dx**2 + dy**2 + dz**2)
            # The test matrix is the plane cd=80+H/2+2V/5.
            cd = 80 + math.degrees(math.atan2(dx, dy)) / 2 + 0.4 * math.degrees(math.asin(dz / r))
            cd_terms.append(cd)
            lux_terms.append(cd * -dz / r**3)
        self.assertAlmostEqual(group.intensity(2, 20, 0), sum(cd_terms))
        self.assertAlmostEqual(group.horizontal_lux(2, 20, 0), sum(lux_terms))
        with self.assertRaises(OutOfDomain):
            group.vertical_lux(60, 1, 0)

    def test_reject_unsupported_and_invalid_ies(self):
        transforms = [
            lambda s: s.replace("TILT=NONE", "TILT=INCLUDE"),
            lambda s: s.replace("3 3 3 2", "3 3 2 2"),
            lambda s: s.replace(" 2 3 3", " -2 3 3"),
            lambda s: s.replace("10 20 30", "10 inf 30"),
            lambda s: s.replace("-80 0 80", "-80 -80 80"),
            lambda s: s.replace("50 60 70", "50 60"),
            lambda s: s + "17",
        ]
        for transform in transforms:
            with self.subTest(transform=transform), self.assertRaises(ValueError):
                self.fixture(transform)

    def test_260114_ignores_only_the_lamp_outside_its_measured_angles(self):
        grid = self.fixture()
        # The target is on the right lamp's axis. The left lamp points away from it.
        left = Lamp(grid, -1, 0, .75, yaw_deg=170)
        right = Lamp(grid, 1, 0, .75)
        group = LampGroup([left, right])
        target = (1, 10, .75)
        self.assertAlmostEqual(group.radial_lux_260114(*target), 80 / 100)
        self.assertAlmostEqual(group.high_beam_intensity(*target), 80 / 100 * 104)
        self.assertEqual(group.radial_lux_260114(0, 1, 100), 0)
        self.assertEqual(group.high_beam_intensity(0, 1, 100), 0)
        with self.assertRaises(OutOfDomain):
            group.radial_lux(*target)

    def test_measurement_limits_are_reported(self):
        grid = self.fixture()
        meta = grid.metadata()
        self.assertTrue(meta["covers_s2_3_2_2_angles"])
        self.assertFalse(meta["sampling_at_most_0_01_deg"])
        self.assertEqual(meta["max_step_deg"], 80)


if __name__ == "__main__":
    unittest.main()
