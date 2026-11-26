"""S.5.2.4.4, Fig. S.11. Integrate vertical illuminance over the 50 m screen."""
from math import isfinite


# Rows follow the printed figure, top to bottom. Columns run vehicle left to right.
LOW = (
    (.2, .2, .2, .2, .2, .2, .2, .2),
    (.2, .2, .5, .5, .5, .5, .5, .2),
    (.2, .2, .5, 1., 1., 1., .5, .2),
    (.2, .2, .5, 1., 1., 1., .5, .2),
    (.2, .2, .5, .5, .5, .5, .5, .2),
)
ADB = ((.2,), (.5,), (1.,), (1.,), (.5,))


def weighted_flux(vertical_lux, adb=False):
    """Return midpoint estimate (lm) and the change on doubling grid density."""
    weights = ADB if adb else LOW
    xmin, xmax = (-4.2, -1.75) if adb else (-7.9, 1.3)
    cell_width = (xmax - xmin) / len(weights[0])
    cell_height = (1.62 - .93) / 5

    def integrate(subdivisions):
        total = 0.
        for row, row_weights in enumerate(weights):
            for col, weight in enumerate(row_weights):
                for iz in range(subdivisions):
                    z = 1.62 - cell_height * (row + (iz + .5) / subdivisions)
                    for ix in range(subdivisions):
                        x = xmin + cell_width * (col + (ix + .5) / subdivisions)
                        lux = vertical_lux(x, 50., z)
                        if lux is None or not isfinite(lux) or lux < 0:
                            raise ValueError("眩光积分区域超出有效配光范围")
                        total += weight * lux
        return total * cell_width * cell_height / subdivisions ** 2

    coarse = integrate(8)
    fine = integrate(16)
    return {"flux_lm": fine, "grid_delta_lm": abs(fine - coarse),
            "grid": "16 × 16 samples per weight cell", "source": "S.5.2.4.4 / Fig S.11"}
