"""C-NCAP 2027 附录 S，表 1–8。所有长度指标均以米输入。

功能布尔量是审核或道路验证结论，缺测应省略或填 null。道路测试输入为按
S.3 取得的有效测量。百分数用百分点表示，例如垂直偏差 0.4% 输入 0.4。
"""

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
import math


@dataclass(frozen=True)
class Rule:
    label: str
    mean_m: float
    sd_m: float
    upper_m: float
    weight: float
    adb_vehicle_weight: float
    clause: str


LOW_RULES = {
    "straight": Rule("直道引导距离", 99.94, 11.43, 114.59, 1.5, 0.6, "S.5.2.1.1"),
    "curve": Rule("弯道引导距离", 106.40, 22.01, 134.61, 1.2, 0.48, "S.5.2.1.2"),
    "pedestrian": Rule("左侧行人可见度", 50.13, 10.23, 63.24, 1.5, 0.6, "S.5.2.1.3"),
    "intersection_width": Rule("路口行人探测宽度", 17.74, 2.25, 20.62, 1.5, 0.6, "S.5.2.1.4"),
    "curve_width": Rule("弯道照明宽度", 25.03, 4.65, 30.99, 1.2, 0.48, "S.5.2.1.5"),
}
HIGH_RULES = {
    f"point{i}": Rule(f"照明范围点{i}", mean, sd, upper, weight, weight, "S.5.2.2.1")
    for i, (mean, sd, upper, weight) in enumerate([
        (192.87, 24.22, 223.91, 1.0), (51.05, 18.76, 75.09, 0.4),
        (38.13, 19.54, 63.17, 0.2), (51.05, 18.76, 75.09, 0.4),
        (176.68, 32.91, 218.86, 0.5),
    ], 1)
}
ADB_RULES = {
    "straight": Rule("直道引导距离（路面）", 99.94, 11.43, 114.59, 0.9, 0.9, "S.5.2.3.1"),
    "curve": Rule("弯道引导距离（路面）", 106.40, 22.01, 134.61, 0.72, 0.72, "S.5.2.3.1"),
    "pedestrian": Rule("左侧行人可见度（0.25 m）", 50.13, 10.23, 63.24, 0.9, 0.9, "S.5.2.3.1"),
    "straight_1_5m": Rule("直道引导距离（1.5 m）", 86.02, 16.78, 107.52, 0.9, 0.9, "S.5.2.3.1"),
    "curve_1_5m": Rule("弯道引导距离（1.5 m）", 149.47, 22.31, 178.06, 0.72, 0.72, "S.5.2.3.1"),
    "pedestrian_1_5m": Rule("左侧行人可见度（1.5 m）", 87.81, 15.19, 107.28, 0.9, 0.9, "S.5.2.3.1"),
}
SCENARIOS = tuple(f"{traffic}_{distance}" for traffic in ("oncoming", "following") for distance in (50, 100, 200))

# Positive feature assertions represent reviewed/verified results, not design intent.
_FEATURES = {
    "highway_mode_approved": ("高速自适应近光审核", 0.1, "S.5.2.4.1"),
    "cornering_mode_approved": ("弯道照明审核", 0.1, "S.5.2.4.1"),
    "auto_beam_approved": ("远近光自动切换审核", 0.2, "S.5.2.4.2"),
    "leveling_approved": ("自动前照灯调平审核", 0.1, "S.5.2.4.3"),
    "reverse_projection_approved": ("倒车投影审核", 0.1, "S.5.2.4.12"),
    "turn_projection_approved": ("转向投影审核", 0.1, "S.5.2.4.12"),
    "trajectory_test_passed": ("驾驶轨迹预测道路验证", 0.2, "S.5.2.4.12"),
}
EVIDENCE_FIELDS = {key: label + "（布尔）" for key, (label, _, _) in _FEATURES.items()}
EVIDENCE_FIELDS.update({
    "basic_glare_lm": "基础近光眩光区域加权光通量（lm）",
    "position_lamp_area_cm2": "企业填报的前位置灯透光面面积（cm²）",
})
for _side, _side_label in (("left", "左"), ("right", "右")):
    EVIDENCE_FIELDS.update({
        f"cutoff_vertical_{_side}_pct": _side_label + "灯初始截止线垂直偏差（%）",
        f"cutoff_horizontal_{_side}_pct": _side_label + "灯初始截止线水平偏差（%）",
        f"cutoff_drift_{_side}_mrad": _side_label + "灯截止线垂直位置变化（mrad，上移为正）",
        f"cutoff_sharpness_{_side}": _side_label + "灯最大锐度因子 G",
    })
_ADB_EVIDENCE = {
    "adb_glare_lm": "ADB 50 m 对向场景眩光区域加权光通量（lm）",
    "activation_oncoming_m": "对向道路验证有效激发距离（m，未激发可填 no_activation）",
    "activation_following_m": "跟车道路验证有效激发距离（m，未激发可填 no_activation）",
    "response_oncoming_s": "对向 400 m 响应试验时间（s，已测无响应填 no_response）",
    "response_following_s": "跟车 100 m 响应试验时间（s，已测无响应填 no_response）",
    "single_mask_lanes": "跟车单目标有效遮蔽宽度（车道数，未遮蔽填 not_masked）",
    "multi_mask_passed": "跟车多目标有效遮蔽（布尔）",
    "reflector_masked": "断电静止车辆反射器试验是否检测到遮蔽（布尔）",
}
EVIDENCE_FIELDS.update(_ADB_EVIDENCE)


def _number(value, name, *, signed=False):
    if type(value) not in (int, float):
        raise ValueError(f"{name} 需要 JSON 数值")
    try:
        value = float(value)
    except OverflowError as exc:
        raise ValueError(f"{name} 超出数值范围") from exc
    if not math.isfinite(value) or (not signed and value < 0):
        raise ValueError(f"{name} 超出数值范围")
    return value


def _fields(data, allowed, name):
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ValueError(f"{name} 需要 JSON 对象")
    unknown = data.keys() - set(allowed)
    if unknown:
        raise ValueError(f"{name} 含未知字段 {', '.join(sorted(unknown))}")
    return data


def _measurements(data, rules, name):
    fields = _fields(data, rules, name)
    return {k: _number(v, f"{name}.{k}") for k, v in fields.items() if v is not None}


def _evidence_inputs(data, has_adb):
    allowed = set(EVIDENCE_FIELDS) if has_adb else set(EVIDENCE_FIELDS) - set(_ADB_EVIDENCE)
    fields = _fields(data, allowed, "evidence")
    checked = {}
    for key, value in fields.items():
        if value is None:
            continue
        if key in _FEATURES or key in {"multi_mask_passed", "reflector_masked"}:
            if type(value) is not bool:
                raise ValueError(f"{key} 需要 true / false / null")
        elif key.startswith("activation_") and value == "no_activation":
            pass
        elif key.startswith("response_") and value == "no_response":
            pass
        elif key == "single_mask_lanes" and value == "not_masked":
            pass
        else:
            signed = key.startswith(("cutoff_vertical_", "cutoff_horizontal_", "cutoff_drift_"))
            value = _number(value, key, signed=signed)
            if key in {"position_lamp_area_cm2", "single_mask_lanes"} and value == 0:
                raise ValueError(f"{key} 必须大于零")
        checked[key] = value
    return checked


def _optical_item(group, key, rule, value, has_adb):
    weight = rule.adb_vehicle_weight if has_adb else rule.weight
    score = regulation_score = None
    if value is not None:
        raw = weight
        if value < rule.upper_m:
            z = (value - rule.mean_m) / rule.sd_m
            cdf = 0.5 * math.erfc(-z / math.sqrt(2))
            raw = min(weight, weight * cdf / 0.9)
        # Quantize independently: rounding the PPT score again can change a regulatory result.
        decimal_score = Decimal(str(raw))
        score = float(decimal_score.quantize(Decimal(".001"), rounding=ROUND_HALF_UP))
        regulation_score = float(decimal_score.quantize(Decimal(".01"), rounding=ROUND_HALF_UP))
    return {
        "id": f"{group}.{key}", "group": group, "label": rule.label,
        "value_m": value, "mean_m": rule.mean_m, "sd_m": rule.sd_m,
        "upper_m": rule.upper_m, "score": score, "regulation_score": regulation_score, "max_score": weight,
        "clause": rule.clause, "status": "scored" if score is not None else "not_evaluated",
    }


def _basic_glare(lm, area, has_adb):
    first, second = (0.63, 0.77) if area <= 60 else (0.72, 0.83)
    full_penalty = -0.6 if has_adb else -1.0
    if lm <= 0.26 or lm > second:
        return full_penalty
    return full_penalty / 2 if lm > first else 0


def _adjustments(data, has_adb):
    items = []

    def add(item_id, label, limits, clause, keys, calculation, *, prerequisite=None):
        missing = [key for key in keys if key not in data]
        reason = None
        if prerequisite:
            condition, description = prerequisite
            if not condition:
                reason = description
        if reason is None and missing:
            reason = "缺少 " + ", ".join(missing)
        score = calculation(*(data[key] for key in keys)) if reason is None else None
        items.append({
            "id": item_id, "group": "bonus" if limits[1] > 0 else "penalty", "label": label,
            "min_score": limits[0], "max_score": limits[1], "score": score, "regulation_score": score, "clause": clause,
            "status": "scored" if score is not None else "not_evaluated", "reason": reason,
            "evidence": {key: data.get(key) for key in keys},
        })

    for key, (label, weight, clause) in _FEATURES.items():
        add(key, label, (0, weight), clause, [key], lambda approved: weight if approved else 0)
    area = "position_lamp_area_cm2"
    add("basic_glare", "基础近光对向眩光", (-0.6 if has_adb else -1, 0),
        "S.5.2.4.4 表5" if has_adb else "S.5.2.4.4 表4", ["basic_glare_lm", area],
        lambda lm, cm2: _basic_glare(lm, cm2, has_adb))
    for side, side_label in (("left", "左"), ("right", "右")):
        add(f"cutoff_initial_{side}", side_label + "灯初始截止线位置", (-0.25, 0), "S.5.2.4.5",
            [f"cutoff_vertical_{side}_pct", f"cutoff_horizontal_{side}_pct"],
            lambda vertical, horizontal: -0.25 if abs(vertical) > 0.4 or abs(horizontal) > 1 else 0)
        add(f"cutoff_drift_{side}", side_label + "灯截止线热漂移", (-0.1, 0), "S.5.2.4.6",
            [f"cutoff_drift_{side}_mrad"], lambda delta: -0.1 if delta > 2 or delta < -3 else 0)
        add(f"cutoff_sharpness_{side}", side_label + "灯截止线锐度", (-0.25, 0), "S.5.2.4.7",
            [f"cutoff_sharpness_{side}"], lambda sharpness: -0.25 if sharpness > 0.48 else 0)
    if not has_adb:
        return items
    add("adb_glare", "ADB 50 m 对向眩光", (-0.4, 0), "S.5.2.4.4 表5", ["adb_glare_lm", area],
        lambda lm, cm2: -0.4 if lm > (0.30 if cm2 <= 60 else 0.36) else 0)
    verified = {}
    for traffic, label, threshold in (("oncoming", "对向", 400), ("following", "跟车", 100)):
        activation = f"activation_{traffic}_m"
        distance = data.get(activation)
        verified[traffic] = (isinstance(distance, float) and distance >= threshold,
                             f"需先满足{label} {threshold} m 功能激发试验（S.3.3）")
        add(f"activation_{traffic}", f"ADB {label}激发", (0, 0.25), "S.5.2.4.8 表6", [activation],
            lambda dist: 0.25 if dist != "no_activation" and dist >= threshold else 0)
        add(f"response_{traffic}", f"ADB {label}响应时间", (-0.1, 0), "S.5.2.4.9 表7",
            [f"response_{traffic}_s"], lambda seconds: -0.1 if seconds == "no_response" or seconds >= 1.5 else 0,
            prerequisite=verified[traffic])
    def single_mask(width):
        if width == "not_masked" or width >= 2:
            return 0
        return 0.25 if width < 1 else 0.125

    add("single_mask", "ADB 单目标遮蔽宽度", (0, 0.25), "S.3.3.5.1.1；S.5.2.4.10 表8",
        ["single_mask_lanes"], single_mask, prerequisite=verified["following"])
    add("multi_mask", "ADB 多目标有效遮蔽", (0, 0.25), "S.5.2.4.10 表8",
        ["multi_mask_passed"], lambda passed: 0.25 if passed else 0, prerequisite=verified["following"])
    add("reflector", "ADB 静止车辆反射器试验", (0, 0.2), "S.5.2.4.11",
        ["reflector_masked"], lambda masked: 0 if masked else 0.2, prerequisite=verified["following"])
    return items


def _score_sum(items, field):
    return float(sum((Decimal(str(item[field])) for item in items if item[field] is not None), Decimal(0)))


def evaluate(has_adb, low_beam=None, high_beam=None, adb_scenarios=None, evidence=None):
    """按附录 S 汇总。缺项保留为 None，整车项目齐全时才返回封顶总分。"""
    if type(has_adb) is not bool:
        raise ValueError("has_adb 必须为 true 或 false")
    evidence = _evidence_inputs(evidence, has_adb)
    items = []
    for group, data, rules in (("low_beam", low_beam, LOW_RULES), ("high_beam", high_beam, HIGH_RULES)):
        values = _measurements(data, rules, group)
        for key, rule in rules.items():
            items.append(_optical_item(group, key, rule, values.get(key), has_adb))
    scenarios = _fields(adb_scenarios, SCENARIOS if has_adb else (), "adb_scenarios")
    if has_adb:
        scenarios = {s: _measurements(scenarios.get(s), ADB_RULES, s) for s in SCENARIOS}
        for key, rule in ADB_RULES.items():
            values = {s: scenarios[s].get(key) for s in SCENARIOS}
            missing = [s for s, v in values.items() if v is None]
            value = None if missing else math.fsum(v / len(SCENARIOS) for v in values.values())
            item = _optical_item("adb", key, rule, value, has_adb)
            item.update(scenario_values_m=values, missing_scenarios=missing)
            items.append(item)
    optical_groups = {group: [item for item in items if item["group"] == group]
                      for group in dict.fromkeys(item["group"] for item in items)}
    items.extend(_adjustments(evidence, has_adb))
    missing = [item["id"] for item in items if item["score"] is None]
    report = {"items": items, "missing": missing, "complete": not missing,
              "score_precision": {"score": "PPT：光学分项三位，再汇总",
                                  "regulation_score": "附录 S：光学分项两位，固定加扣分按表值汇总"}}
    for prefix in ("", "regulation_"):
        field = prefix + "score"
        subtotal = _score_sum(items, field)
        report[prefix + "base_subtotals"] = {
            group: _score_sum(rows, field) if all(row[field] is not None for row in rows) else None
            for group, rows in optical_groups.items()}
        report[prefix + "known_subtotal"] = subtotal
        report[prefix + "total"] = None if missing else min(10, subtotal)
    return report
