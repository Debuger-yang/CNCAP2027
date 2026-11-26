"""Combine installed photometry, measured values and Appendix S scoring."""
import json
from functools import lru_cache
from math import isfinite
from pathlib import Path

from . import geometry, scoring
from .glare import weighted_flux
from .photometry import IESGrid, Lamp, LampGroup, OutOfDomain

PDF_SHA256 = "49d5a008991ec8241f8065bef7f4625a6391d9267410aa59b8518360ea8b16e2"
MODELS = {"radial": "radial_lux", "vertical": "vertical_lux", "horizontal": "horizontal_lux"}
LOW_KEYS = ("straight", "curve", "pedestrian", "intersection_width", "curve_width")
WIDTH_KEYS = ("intersection_width", "curve_width")


def load_config(path):
    def unique(pairs):
        data = {}
        for key, value in pairs:
            if key in data:
                raise ValueError(f"重复字段 {key}")
            data[key] = value
        return data

    return json.loads(Path(path).read_text(encoding="utf-8-sig"), object_pairs_hook=unique)


def mapping(value, label, allowed=None):
    if not isinstance(value, dict):
        raise ValueError(f"{label} 必须是 JSON 对象")
    if allowed is not None:
        unknown = set(value) - set(allowed)
        if unknown:
            raise ValueError(f"{label} 含未知字段 {', '.join(sorted(unknown))}")
    return value


def number(value, key):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value):
        raise ValueError(f"{key} 需要有限数值")
    return value


def load_lamp_pair(settings, name, base_dir, load_grid):
    mapping(settings, name, {"left", "right"})
    if set(settings) != {"left", "right"}:
        raise ValueError(f"{name} 需要分别指定 left 和 right 两侧配光")
    lamps, sources = [], {}
    for side in ("left", "right"):
        spec = settings[side]
        mapping(spec, side, {"ies", "x_m", "y_m", "z_m", "yaw_deg", "pitch_deg"})
        if not all(k in spec for k in ("ies", "x_m", "y_m", "z_m")):
            raise ValueError(f"{name}.{side} 缺 ies、x_m、y_m 或 z_m")
        if not isinstance(spec["ies"], str) or not spec["ies"].strip():
            raise ValueError(f"{side}.ies 需要文件路径")
        path = (Path(base_dir) / spec["ies"]).resolve()
        grid = load_grid(path)
        pos = {k: number(spec[k], k) for k in ("x_m", "y_m", "z_m", "yaw_deg", "pitch_deg") if k in spec}
        if pos["z_m"] <= 0:
            raise ValueError("灯具安装高度 z_m 必须大于 0")
        lamps.append(Lamp(grid, **pos))
        sources[side] = {"ies": str(path), "yaw_deg": 0, "pitch_deg": 0,
                         **pos, "file": grid.metadata()}
    return LampGroup(lamps), sources

def bounded(function):
    def query(x, y, z):
        try:
            return function(x, y, z)
        except OutOfDomain:
            return None
    return query


def calculate(config, base_dir=Path.cwd(), *, load_grid=None, progress=None):
    """Calculate a project, reporting optional progress on the calling thread."""
    mapping(config, "配置", {"project", "has_adb", "measurements", "evidence", "photometry"})
    adb = config.get("has_adb")
    if type(adb) is not bool:
        raise ValueError("请指定 has_adb 为 true 或 false")
    if "project" in config and not isinstance(config["project"], str):
        raise ValueError("project 应填写项目名称")
    measurements = mapping(config.get("measurements", {}), "measurements",
                           {"low_beam", "high_beam", "adb_scenarios"})
    low = dict(mapping(measurements.get("low_beam", {}), "low_beam", LOW_KEYS))
    high = dict(mapping(measurements.get("high_beam", {}), "high_beam", scoring.HIGH_RULES))
    measured_scenes = mapping(measurements.get("adb_scenarios", {}), "adb_scenarios", scoring.SCENARIOS)
    scenes = {name: dict(mapping(data, name, scoring.ADB_RULES)) for name, data in measured_scenes.items()}
    evidence = dict(mapping(config.get("evidence", {}), "evidence", scoring.EVIDENCE_FIELDS))
    optics = mapping(config.get("photometry", {}), "photometry",
                     {"projection", "projection_1_5m", "low_beam", "high_beam", "adb_scenarios", "step_m",
                      "max_distance_m", "half_width_m"})
    adb_ies = mapping(optics.get("adb_scenarios", {}), "photometry.adb_scenarios", scoring.SCENARIOS)
    if not adb and (scenes or adb_ies):
        raise ValueError("has_adb=false 与 ADB 工况输入冲突")
    projection = optics.get("projection")
    if ("low_beam" in optics or adb_ies) and projection not in MODELS:
        raise ValueError("IES 路照计算需要指定 projection 为 radial、vertical 或 horizontal")
    upper_projection = optics.get("projection_1_5m", projection)
    if adb_ies and upper_projection not in {"radial", "vertical"}:
        raise ValueError("请为 ADB 高层指定 projection_1_5m 为 radial 或 vertical")
    options = {k: number(optics[k], k) for k in ("step_m", "max_distance_m", "half_width_m") if k in optics}
    if not (.01 <= options.get("step_m", .25) <= 2 and
            10 <= options.get("max_distance_m", 350) <= 2000 and
            2 <= options.get("half_width_m", 80) <= 200):
        raise ValueError("搜索参数范围：步长 0.01–2 m，前向距离 10–2000 m，横向半宽 2–200 m")
    # Plan all supplied lamp groups before reading the first IES. Scenarios
    # follow Appendix S even when the project JSON lists them in another order.
    tasks = []
    for name, target, label in (("low_beam", low, "基础近光"), ("high_beam", high, "远光")):
        if name in optics:
            tasks.append((name, optics[name], target, label))
    for name in scoring.SCENARIOS:
        if name in adb_ies:
            traffic, distance = name.split("_")
            label = f"ADB {'对向' if traffic == 'oncoming' else '跟车'} {distance} m"
            tasks.append((name, adb_ies[name], scenes.setdefault(name, {}), label))
    for name, _, target, _ in tasks:
        for key, value in target.items():
            if value is None:
                continue
            if name == "low_beam" and key in WIDTH_KEYS:
                scoring._number(value, f"{name}.{key}")
                continue
            raise ValueError(f"{name}.{key} 同时提供了测量值和 IES，请选择数据来源")

    inputs, details = {}, {}
    if load_grid is None:
        load_grid = lru_cache(maxsize=2)(IESGrid.load)

    def glare(name, lamps, is_adb):
        field = "adb_glare_lm" if is_adb else "basic_glare_lm"
        # Allow traceable laboratory glare flux to take precedence over an estimate.
        if evidence.get(field) is not None:
            details[name] = {"source": "输入的眩光测量值", "flux_lm": evidence[field]}
            return
        try:
            result = weighted_flux(bounded(lamps.vertical_lux), adb=is_adb)
        except ValueError as exc:
            details[name] = {"status": "not_evaluated", "reason": str(exc)}
            return
        details[name] = result
        area = evidence.get("position_lamp_area_cm2")
        if area is not None:
            area = number(area, "position_lamp_area_cm2")
            thresholds = ([.30 if area <= 60 else .36] if is_adb else
                          ([.26, .63, .77] if area <= 60 else [.26, .72, .83]))
            if any(abs(result["flux_lm"] - t) <= result["grid_delta_lm"] for t in thresholds):
                result.update(status="not_evaluated", reason="积分步长加密的差值覆盖眩光扣分阈值，需实测通量")
                return
        evidence[field] = result["flux_lm"]

    for name, settings, target, label in tasks:
        if progress is not None:
            progress(f"正在测量：{label}")
        lamps, inputs[name] = load_lamp_pair(settings, name, base_dir, load_grid)
        if name == "low_beam":
            if projection == "radial":
                rows = geometry.low_beam_260114(lamps.radial_lux_260114)
            else:
                rows = geometry.low_beam(bounded(getattr(lamps, MODELS[projection])), **options)
            for key in WIDTH_KEYS:
                if target.get(key) is not None:
                    rows[key] = {"status": "ok", "value_m": target[key], "source": "录入实测值",
                                 "ies_estimate": rows[key]}
        elif name == "high_beam":
            rows = geometry.high_beam(bounded(lamps.high_beam_intensity))
            for item in rows.values():
                item["method"] = "2024（260114）五点评价：逐灯径向照度之和按左灯距离平方换算为光强"
        else:
            lower = getattr(lamps, MODELS[projection])
            upper = getattr(lamps, MODELS[upper_projection])

            def adb_lux(x, y, z):
                return (upper if z == 1.5 else lower)(x, y, z)

            rows = geometry.adb_scene(bounded(adb_lux),
                                      **{k: v for k, v in options.items() if k != "half_width_m"})
        target.update((key, item["value_m"]) for key, item in rows.items())
        details[name] = rows
        if name == "low_beam":
            glare("basic_glare", lamps, False)
        elif name == "oncoming_50":
            glare("adb_glare_50m", lamps, True)

    if progress is not None:
        progress("正在评分：C-NCAP 2027 附录 S")
    report = scoring.evaluate(has_adb=adb, low_beam=low, high_beam=high, adb_scenarios=scenes, evidence=evidence)
    has_measured_optics = False
    for item in report["items"]:
        group = item["group"]
        if group not in {"low_beam", "high_beam"} or item["value_m"] is None:
            continue
        key = item["id"].split(".")[-1]
        if measurements.get(group, {}).get(key) is not None:
            item["value_source"] = "measured"
            has_measured_optics = True
        else:
            item["value_source"] = "ies"
    assessment = "按录入的测试数据评算"
    if inputs:
        assessment = "IES 工程估算与录入实测值" if has_measured_optics else "IES 工程估算"
    report.update({"project": config.get("project", ""), "basis": "C-NCAP 2027 附录 S",
                   "source_pdf_sha256": PDF_SHA256, "has_adb": adb, "projection": projection,
                   "projection_1_5m": upper_projection if adb_ies else None,
                   "inputs": inputs, "measurement_details": details, "input_config": config,
                   "assessment": assessment})
    return report
