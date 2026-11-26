"""C-NCAP 2027 Appendix S, figures S.3–S.9; coordinates in metres.

x increases to the vehicle's right, y forward from the headlamp reference
plane, and z upwards from road level. The caller supplies the illuminance
field on the indicated plane, or the combined directional intensity for HB.
The 260114 comparison path takes per-lamp radial illuminance with zero
contribution outside each lamp's measured angles. The engineering interpolation
and ADB paths use None for an unmeasured direction.

Isolux intersections are engineering estimates: compare a grid with maximum
interval step_m to its midpoint-refined grid, then linearly interpolate.
For longitudinal distance, verify the farthest falling crossing's y coordinate
to 0.05 m on the two grids. Record changes to interior crossings separately.
For width, the engineering convention is the closed 3 lx bright interval
containing x=0. A unique off-centre interval is usable on a fully observed
scan. Verify the selected edges and width after refining to at most 0.005 m.
Features narrower than the fine-grid interval can still go undetected.
"""

import math
from statistics import fmean


_SLOPE = math.tan(math.radians(5))
_COSINE = math.cos(math.radians(5))
_LEVELS = (1.0, 3.0, 5.0)
_GRID_TOLERANCE_M = 0.05
_WIDTH_CHECK_STEP_M = 0.005
_MAX_LINE_SEGMENTS = 200_000
_LINE_ITEMS = (
    ("straight", (0.0, 1.75, 3.5), 0.0, False),
    ("curve", (0.0, 1.75, 3.5), 0.0, True),
    ("pedestrian", (-3.5, -5.25, -7.0), 0.25, False),
)
_POINTS = (
    (0.0, 100.0, 0.75), (-20.0, 100.0, 0.75), (0.0, 100.0, 9.75),
    (20.0, 100.0, 0.75), (0.0, 100.0, -1.25),
)


def _positions(start, stop, step):
    if not all(math.isfinite(v) for v in (start, stop, step)) or step <= 0 or stop <= start:
        raise ValueError("扫描范围和步长必须是有限数，范围长度和步长必须大于零")
    count = (stop - start) / step
    if count > _MAX_LINE_SEGMENTS:
        raise ValueError("单条扫描线超过 200000 段，请缩小扫描范围或增大步长")
    count = math.ceil(count)
    return [start + (stop - start) * (i / count) for i in range(count + 1)]


def _reading(value):
    if value is None:
        return None
    if isinstance(value, bool) or not math.isfinite(value) or value < 0:
        raise ValueError("光度读数必须为非负有限数，角域缺测请传 None")
    return value


def _observed(positions, readings):
    known = [i for i, val in enumerate(readings) if val is not None]
    if not known:
        return [], [], {"reason": "整条采样线缺少配光数据"}
    a, b = known[0], known[-1]
    meta = {"coverage_m": [positions[a], positions[b]],
            "missing_before": a > 0, "missing_after": b < len(positions) - 1}
    if b - a + 1 != len(known):
        return [], [], {**meta, "reason": "有效采样范围内存在配光数据缺口"}
    return positions[a:b + 1], readings[a:b + 1], meta


def _half_step_grid(positions):
    fine = []
    for left, right in zip(positions, positions[1:]):
        fine.extend((left, (left + right) / 2))
    fine.append(positions[-1])
    return fine


def _sample_grids(positions, measure):
    """Each coarse point is a fine-grid point and is evaluated only once."""
    fine = _half_step_grid(positions)
    readings = [_reading(measure(t)) for t in fine]
    return _observed(positions, readings[::2]), _observed(fine, readings)


def _verified_sample(coarse, fine, step_m, *, longitudinal=False):
    a, b = coarse["value_m"], fine["value_m"]
    delta = None if a is None or b is None else abs(b - a)
    edge_delta = None
    if "edges_m" in coarse and "edges_m" in fine:
        edge_delta = max(abs(x-y) for x, y in zip(coarse["edges_m"], fine["edges_m"]))
    check = {"coarse_step_m": step_m, "fine_step_m": step_m / 2,
             "coarse_value_m": a, "fine_value_m": b, "abs_delta_m": delta,
             "coarse_crossings": coarse.get("crossings"), "fine_crossings": fine.get("crossings"),
             "max_edge_delta_m": edge_delta, "engineering_tolerance_m": _GRID_TOLERANCE_M}
    for name, sample in (("coarse", coarse), ("fine", fine)):
        if "reason" in sample:
            check[name + "_reason"] = sample["reason"]
    result = {**fine, "refinement": check}
    if (a is None) != (b is None):
        return {**result, "value_m": None, "reason": "半步长复核时交点可用性改变"}
    changed = check["coarse_crossings"] != check["fine_crossings"]
    if not longitudinal and coarse.get("selection") != fine.get("selection"):
        return {**result, "value_m": None, "reason": "加密前后选定亮区的依据发生改变"}
    if max(delta or 0, edge_delta or 0) > _GRID_TOLERANCE_M + 1e-10:
        return {**result, "value_m": None, "reason": "半步长复核差异超过工程容差 0.05 m"}
    if longitudinal and changed and b is not None:
        check["note"] = "沿线交点数改变，仅最远下降交点的位置通过半步长复核"
    if not longitudinal and changed and b is not None:
        check["note"] = "横线交点数改变，选定亮区的两侧边界和宽度通过复核"
    return result


def _interpolate(x1, x2, val1, val2, level):
    return x1 + (x2 - x1) * (level - val1) / (val2 - val1)


def _longitudinal(positions, readings, level, meta):
    sample = {**meta, "threshold_lx": level, "value_m": None}
    if "reason" in meta:
        return sample
    if meta["missing_after"] or readings[-1] >= level:
        return {**sample, "reason": "远端缺测或仍达到阈值，无法确定最远交点"}
    crossings = sum((a >= level) != (b >= level) for a, b in zip(readings, readings[1:]))
    sample["crossings"] = crossings
    exits = [_interpolate(positions[i], positions[i+1], val, readings[i+1], level)
             for i, val in enumerate(readings[:-1]) if val >= level > readings[i+1]]
    if not exits:
        return {**sample, "reason": "扫描范围内未包围该等照度线交点"}
    return {**sample, "value_m": exits[-1], "falling_crossings": len(exits)}


def _width(positions, readings, meta):
    sample = {**meta, "threshold_lx": 3.0, "value_m": None}
    if "reason" in meta:
        return sample
    edges = [_interpolate(positions[i], positions[i+1], val, readings[i+1], 3.0)
             for i, val in enumerate(readings[:-1]) if (val >= 3) != (readings[i+1] >= 3)]
    # A null endpoint marks a bright region clipped at that edge of coverage.
    bounds = ([None] if readings[0] >= 3 else []) + edges + ([None] if readings[-1] >= 3 else [])
    intervals = [bounds[i:i + 2] for i in range(0, len(bounds), 2)]
    sample.update(crossings=len(edges), candidate_intervals_m=intervals)
    for left, right in intervals:
        if left is not None and right is not None and left <= 0 <= right:
            return {**sample, "selection": "含车辆中心线的连续亮区", "edges_m": [left, right],
                    "value_m": right - left}
    if any(None in interval for interval in intervals):
        return {**sample, "reason": "横向有效范围未包围 3 lx 光型的两侧边界，且无独立闭合的中心亮区"}
    if len(intervals) == 1 and not (meta["missing_before"] or meta["missing_after"]):
        left, right = intervals[0]
        return {**sample, "selection": "完整横线上唯一闭合亮区", "edges_m": [left, right],
                "value_m": right - left}
    return {**sample, "reason": "无中心线连续亮区，且未能在完整测线上确定唯一闭合亮区"}


def _width_scan(positions, measure):
    """Reuse existing readings on each refinement. Small dark gaps split a bright interval."""
    if 2 * (len(positions) - 1) > _MAX_LINE_SEGMENTS:
        raise ValueError("横向宽度复核超过 200000 段，请缩小横向范围或增大初始步长")
    readings = [_reading(measure(x)) for x in positions]
    coarse = _width(*_observed(positions, readings))
    step = positions[1] - positions[0]
    initial_step = step
    history = []
    while True:
        fine_positions = _half_step_grid(positions)
        fine_readings = [None] * len(fine_positions)
        fine_readings[::2] = readings
        fine_readings[1::2] = [_reading(measure(x)) for x in fine_positions[1::2]]
        fine = _width(*_observed(fine_positions, fine_readings))
        sample = _verified_sample(coarse, fine, step)
        check = sample["refinement"]
        history.append({"step_m": step / 2, "crossings": fine.get("crossings"),
                        "edges_m": fine.get("edges_m"), "value_m": fine["value_m"],
                        "reason": sample.get("reason")})
        # A coarse pair of agreeing widths can still miss an interior dark gap.
        # Refine every width to this engineering resolution, then further if
        # the selected edges or their separation have not yet stabilized.
        if step / 2 <= _WIDTH_CHECK_STEP_M + 1e-10:
            if sample["value_m"] is not None or (coarse["value_m"] is None and fine["value_m"] is None):
                break
        if 2 * (len(fine_positions) - 1) > _MAX_LINE_SEGMENTS:
            sample.update(value_m=None, reason="达到横向采样上限，3 lx 宽度仍未通过复核")
            break
        positions, readings, coarse = fine_positions, fine_readings, fine
        step /= 2
    check.update(initial_step_m=initial_step, target_step_m=_WIDTH_CHECK_STEP_M, history=history)
    return sample


def _summarize(samples, clause):
    complete = all(s["value_m"] is not None for s in samples)
    result = {"status": "ok" if complete else "not_evaluated",
              "value_m": fmean(s["value_m"] for s in samples) if complete else None,
              "reason": "" if complete else "部分必需测量无有效结果，见 samples",
              "source": clause, "samples": samples}
    if "refinement" in samples[0]:
        # Keep candidate averages visible even when a component rejects scoring.
        coarse = [s["refinement"]["coarse_value_m"] for s in samples]
        fine = [s["refinement"]["fine_value_m"] for s in samples]
        a = fmean(coarse) if None not in coarse else None
        b = fmean(fine) if None not in fine else None
        # Adaptive width scans can finish at different steps on each test line.
        coarse_steps = {s["refinement"]["coarse_step_m"] for s in samples}
        fine_steps = {s["refinement"]["fine_step_m"] for s in samples}
        result["refinement"] = {"coarse_step_m": next(iter(coarse_steps)) if len(coarse_steps) == 1 else None,
                                "fine_step_m": next(iter(fine_steps)) if len(fine_steps) == 1 else None,
                                "coarse_candidate_m": a, "fine_candidate_m": b,
                                "abs_delta_m": abs(a-b) if a is not None and b is not None else None,
                                "verified_samples": sum(s["value_m"] is not None for s in samples),
                                "required_samples": len(samples), "engineering_tolerance_m": _GRID_TOLERANCE_M}
    return result


def _distances(illuminance, positions, offsets, z, angled, clause):
    samples = []
    step = positions[1] - positions[0]
    for offset in offsets:
        # S.5.2.1.2: perpendicular separation of parallel guide lines.
        intercept = offset / _COSINE if angled else offset
        slope = _SLOPE if angled else 0.0
        coarse, fine = _sample_grids(positions, lambda y: illuminance(intercept + slope*y, y, z))
        for level in _LEVELS:
            checks = [_longitudinal(grid, values, level, coverage)
                      for grid, values, coverage in (coarse, fine)]
            samples.append({"line_offset_m": offset, "angle_right_deg": 5 if angled else 0,
                            "z_m": z, **_verified_sample(*checks, step, longitudinal=True)})
    return _summarize(samples, clause)


def low_beam(illuminance, *, step_m=0.25, max_distance_m=350.0, half_width_m=80.0):
    """Five basic low-beam items, from one as-mounted illuminance field in lx."""
    y_values = _positions(0, max_distance_m, step_m)
    # Generate symmetric half grids so that x=0 is always present.
    half_grid = _positions(0, half_width_m, step_m)
    x_values = [-x for x in reversed(half_grid[1:])] + half_grid
    result = {}
    for index, (key, offsets, z, angled) in enumerate(_LINE_ITEMS, 1):
        result[key] = _distances(illuminance, y_values, offsets, z, angled, f"S.5.2.1.{index}")
    width_lines = (("intersection_width", (10, 20), 0.25), ("curve_width", (30, 40, 50), 0.0))
    for index, (key, distances, z) in enumerate(width_lines, 4):
        samples = []
        for y in distances:
            sample = _width_scan(x_values, lambda x: illuminance(x, y, z))
            samples.append({"y_m": y, "z_m": z, **sample})
        result[key] = _summarize(samples, f"S.5.2.1.{index}")
    return result


def _ray_260114(measure, threshold, start, stop, coarse_step):
    """260114 distOnLine scans to the first sub-threshold sample on each pass."""
    passes = []
    for step in (coarse_step, 1.0, 0.1):
        cursor = start
        while cursor < stop:
            reading = _reading(measure(cursor))
            if reading is None:
                return {"value_m": None, "reason": "扫描点缺少照度读数", "missing_at_m": cursor,
                        "passes": passes}
            if reading < threshold:
                edge = cursor - step
                break
            cursor += step
        else:
            edge = stop
        passes.append({"start_m": start, "stop_m": stop, "step_m": step, "result_m": edge,
                       "first_sample_below_threshold": cursor == start and start < stop})
        start = edge - step
    return {"value_m": edge, "passes": passes}


def low_beam_260114(illuminance):
    """Use 260114's fixed ray origins and three searches for the five base measurements."""
    result = {}
    # Twice the original task defaults. These are scan endpoints, independent of 2027 scoring.
    distance_limits = (233.30, 269.22, 126.48)
    for index, ((key, offsets, z, angled), stop) in enumerate(zip(_LINE_ITEMS, distance_limits), 1):
        samples = []
        slope = _SLOPE if angled else 0.0
        for offset in offsets:
            intercept = offset * _COSINE if angled else offset
            for level in _LEVELS:
                ray = _ray_260114(lambda y: illuminance(intercept + slope*y, y, z),
                                  level, 50.0, stop, 15.0)
                samples.append({"line_offset_m": offset, "x_at_y0_m": intercept,
                                "angle_right_deg": 5 if angled else 0, "z_m": z,
                                "threshold_lx": level, **ray})
        result[key] = _summarize(samples, f"S.5.2.1.{index} / CNCAP 260114")

    width_lines = (("intersection_width", 0.0, (10, 20), 0.25),
                   ("curve_width", 5.0, (30, 40, 50), 0.0))
    for index, (key, origin, distances, z) in enumerate(width_lines, 4):
        samples = []
        for y in distances:
            left = _ray_260114(lambda t: illuminance(origin - t, y, z), 3.0, -5.0, 250.0, 5.0)
            right = _ray_260114(lambda t: illuminance(origin + t, y, z), 3.0, -5.0, 250.0, 5.0)
            sample = {"x_origin_m": origin, "y_m": y, "z_m": z, "threshold_lx": 3.0,
                      "left_ray": left, "right_ray": right, "value_m": None}
            a, b = left["value_m"], right["value_m"]
            if a is None or b is None:
                sample["reason"] = "横向扫描点缺少照度读数"
            else:
                width = a + b
                sample.update(edges_m=[origin - a, origin + b], raw_width_m=width)
                if width >= 0:
                    sample["value_m"] = width
                else:
                    sample["reason"] = "260114 扫描返回负宽度，两侧边界未构成有效亮区"
            samples.append(sample)
        result[key] = _summarize(samples, f"S.5.2.1.{index} / CNCAP 260114")
    return result


def adb_scene(illuminance, *, step_m=0.25, max_distance_m=350.0):
    """Six unrounded measurements for one of the six S.5.2.3.1 scenarios."""
    positions = _positions(0, max_distance_m, step_m)
    result = {}
    for key, offsets, z, angled in _LINE_ITEMS:
        for metric, plane in ((key, z), (key + "_1_5m", 1.5)):
            result[metric] = _distances(illuminance, positions, offsets, plane, angled, "S.5.2.3.1")
    return result


def high_beam(intensity):
    """Five point ranges Ri=sqrt(Ii/3 lx) per formula 6 and figure S.9."""
    result = {}
    for index, point in enumerate(_POINTS, 1):
        cd = _reading(intensity(*point))
        sample = {"position_m": list(point), "intensity_cd": cd, "threshold_lx": 3.0,
                  "value_m": None if cd is None else math.sqrt(cd / 3.0)}
        if cd is None:
            sample["reason"] = "远光评价点在配光有效角域以外"
        result[f"point{index}"] = _summarize([sample], "S.5.2.2.1 / 图 S.9 / 公式 6")
    return result
