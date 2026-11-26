"""Measured inputs and score details for the standalone vehicle lighting window."""
from copy import deepcopy
import json
import tkinter as tk
from tkinter import messagebox, ttk

from . import scoring
from .output import GROUP_NAMES, display_score, summaries


_SPECIAL_VALUES = {
    "no_activation": "未激发", "no_response": "无响应", "not_masked": "未遮蔽",
}
_VALUE_SOURCES = {"measured": "录入实测值", "ies": "IES 工程估算"}


def _scenario_name(key):
    traffic, distance = key.split("_")
    return f"{'对向' if traffic == 'oncoming' else '跟车'} {distance} m"


def _display(value):
    if value is None:
        return ""
    if type(value) is bool:
        return "是" if value else "否"
    return _SPECIAL_VALUES.get(value, str(value))


def _special_choice(key):
    if key.startswith("activation_"):
        return "no_activation"
    if key.startswith("response_"):
        return "no_response"
    if key == "single_mask_lanes":
        return "not_masked"
    return None


def _read_field(text, key, evidence, adb):
    text = text.strip()
    if not text:
        return None
    if evidence:
        special = _special_choice(key)
        if special and text in (special, _SPECIAL_VALUES[special]):
            return special
        if key in scoring._FEATURES or key in {"multi_mask_passed", "reflector_masked"}:
            if text not in {"是", "否"}:
                raise ValueError("请选择是、否或留空")
            return text == "是"
    try:
        value = float(text)
    except ValueError:
        raise ValueError("请填写有效数值") from None
    if evidence:
        return scoring._evidence_inputs({key: value}, adb)[key]
    return scoring._number(value, key)


def edit_inputs(parent, config):
    """Edit a copy of config and return it on acceptance, or None on cancellation."""
    adb = config.get("has_adb", False)
    dialog = tk.Toplevel(parent)
    dialog.title("实测值与功能审核 — C-NCAP 2027")
    dialog.transient(parent)
    outer = ttk.Frame(dialog, padding=10)
    outer.pack(fill="both", expand=True)
    ttk.Label(outer, text="未测或资料待补请留空。已测为零时填写 0。").pack(anchor="w", pady=(0, 8))
    tabs = ttk.Notebook(outer)
    tabs.pack(fill="both", expand=True)
    fields = []
    result = None

    def page(title):
        frame = ttk.Frame(tabs, padding=10)
        tabs.add(frame, text=title)
        return frame

    def add_field(section, path, label, row, column, tab, *, width=14):
        source = config
        for part in path[:-1]:
            source = source.get(part, {})
        key = path[-1]
        value = source.get(key)
        variable = tk.StringVar(dialog, value=_display(value))
        if path[0] == "evidence" and (key in scoring._FEATURES or key in {"multi_mask_passed", "reflector_masked"}):
            widget = ttk.Combobox(section, textvariable=variable, values=("", "是", "否"),
                                  width=width, state="readonly")
        elif path[0] == "evidence" and _special_choice(key):
            widget = ttk.Combobox(section, textvariable=variable, width=width,
                                  values=("", _SPECIAL_VALUES[_special_choice(key)]))
        else:
            widget = ttk.Entry(section, textvariable=variable, width=width)
        widget.grid(row=row, column=column, sticky="ew", padx=4, pady=3)
        fields.append((path, label, variable, widget, tab))

    optical = page("近光与远光")
    for index, (group, rules) in enumerate((("low_beam", scoring.LOW_RULES),
                                            ("high_beam", scoring.HIGH_RULES))):
        frame = ttk.LabelFrame(optical, text=f"{GROUP_NAMES[group]}（m）", padding=8)
        frame.grid(row=0, column=index, sticky="new", padx=4)
        optical.columnconfigure(index, weight=1)
        frame.columnconfigure(1, weight=1)
        for row, (key, rule) in enumerate(rules.items()):
            ttk.Label(frame, text=rule.label).grid(row=row, column=0, sticky="w")
            add_field(frame, ("measurements", group, key), rule.label, row, 1, optical)
    ttk.Label(optical, wraplength=490, justify="left",
              text="已加载近光 IES 时，可单独填写路口行人探测宽度或弯道照明宽度的实测平均值（m）。"
                   "填写的项目按实测值评分，分项明细保留 IES 估算结果。留空时采用 IES 估算。"
              ).grid(row=1, column=0, columnspan=2, sticky="w", padx=4, pady=8)

    if adb:
        scenes = page("ADB 六工况")
        ttk.Label(scenes, text="测量指标（m）").grid(row=0, column=0, sticky="w")
        for column, scene in enumerate(scoring.SCENARIOS, 1):
            title = _scenario_name(scene)
            ttk.Label(scenes, text=title).grid(row=0, column=column, padx=4)
            scenes.columnconfigure(column, weight=1)
            for row, (key, rule) in enumerate(scoring.ADB_RULES.items(), 1):
                if column == 1:
                    ttk.Label(scenes, text=rule.label).grid(row=row, column=0, sticky="w")
                path = ("measurements", "adb_scenarios", scene, key)
                add_field(scenes, path, f"{title} · {rule.label}", row, column, scenes, width=9)

    basic = page("功能与眩光")
    cutoff = page("截止线")
    road = page("ADB 道路验证") if adb else None
    for key, full_label in scoring.EVIDENCE_FIELDS.items():
        if key in scoring._ADB_EVIDENCE:
            if not adb:
                continue
            tab = basic if key == "adb_glare_lm" else road
        else:
            tab = cutoff if key.startswith("cutoff_") else basic
        label = full_label.removesuffix("（布尔）")
        if _special_choice(key):
            label = label.split("，")[0] + "）"
        row = tab.grid_size()[1]
        ttk.Label(tab, text=label).grid(row=row, column=0, sticky="w", padx=4)
        add_field(tab, ("evidence", key), label, row, 1, tab, width=22)
        tab.columnconfigure(1, weight=1)

    def accept():
        nonlocal result
        edited = deepcopy(config)
        for path, label, variable, widget, tab in fields:
            try:
                value = _read_field(variable.get(), path[-1], path[0] == "evidence", adb)
            except ValueError as error:
                tabs.select(tab)
                widget.focus_set()
                messagebox.showerror("输入有误", f"{label}\n{str(error).replace(path[-1], label)}",
                                     parent=dialog)
                return
            target = edited
            for part in path[:-1]:
                if part not in target and value is None:
                    break
                target = target.setdefault(part, {})
            else:
                if value is not None or path[-1] in target:
                    target[path[-1]] = value
        result = edited
        dialog.destroy()

    buttons = ttk.Frame(outer)
    buttons.pack(fill="x", pady=(8, 0))
    ttk.Button(buttons, text="取消", command=dialog.destroy).pack(side="right")
    ttk.Button(buttons, text="确定", command=accept).pack(side="right", padx=6)
    dialog.bind("<Escape>", lambda event: dialog.destroy())
    dialog.wait_visibility()
    dialog.grab_set()
    dialog.wait_window()
    return result


def _item_details(report, item):
    group = item["group"]
    metric = item["id"].split(".")[-1]
    measured = report.get("measurement_details", {})
    reasons = []
    details = {}
    if group == "adb":
        for scene, value in item["scenario_values_m"].items():
            row = measured.get(scene, {}).get(metric, {})
            title = _scenario_name(scene)
            details[title] = row or {"实测距离（m）": value}
            if value is None:
                reasons.append(f"{title} {row.get('reason') or '缺少有效测量值'}")
    elif group in {"low_beam", "high_beam"}:
        details = measured.get(group, {}).get(metric, {})
        if details.get("reason"):
            reasons.append(details["reason"])
    else:
        details = {scoring.EVIDENCE_FIELDS.get(k, k): _display(v) or "待补"
                   for k, v in item.get("evidence", {}).items()}
        glare_key = {"basic_glare": "basic_glare", "adb_glare": "adb_glare_50m"}.get(item["id"])
        if glare_key in measured:
            details["眩光积分"] = measured[glare_key]
            if measured[glare_key].get("reason"):
                reasons.append(measured[glare_key]["reason"])
    if item.get("reason"):
        reason = item["reason"]
        for key, label in scoring.EVIDENCE_FIELDS.items():
            reason = reason.replace(key, label.removesuffix("（布尔）"))
        reasons.append(reason)
    if item["score"] is None and not reasons:
        reasons.append("缺少有效测量值")
    return "；".join(reasons), details


def show_results(parent, report, on_export):
    """Open a nonmodal result inspector. The caller owns its lifetime."""
    dialog = tk.Toplevel(parent)
    dialog.title("计算结果 — C-NCAP 2027")
    dialog.geometry(f"{min(1020, dialog.winfo_screenwidth() - 40)}x{min(690, dialog.winfo_screenheight() - 100)}")
    frame = ttk.Frame(dialog, padding=8)
    frame.pack(fill="both", expand=True)
    ttk.Label(frame, text=f"{report['assessment']}    未评定 {len(report['missing'])} 项").pack(anchor="w")
    summary = ttk.Frame(frame)
    summary.pack(anchor="w", pady=(2, 6))
    for i, title in enumerate(("计分口径", "PPT 三位", "规程计分")):
        ttk.Label(summary, text=title).grid(row=i, column=0, padx=(0, 18), sticky="w")
    for col, (label, ppt, regulation) in enumerate(summaries(report), 1):
        for i, text in enumerate((label, display_score(ppt), display_score(regulation, regulation=True))):
            ttk.Label(summary, text=text).grid(row=i, column=col, padx=8)
    panes = ttk.Panedwindow(frame, orient="vertical")
    panes.pack(fill="both", expand=True)
    table = ttk.Frame(panes)
    panes.add(table, weight=3)
    cols = ("group", "label", "metres", "source", "score", "regulation_score", "range", "clause")
    tree = ttk.Treeview(table, columns=cols, show="headings", selectmode="browse")
    headers = ("组别", "评价项目", "测量值（m）", "取值来源", "PPT 三位", "规程计分", "分值范围", "规程条款")
    for col, title, width in zip(cols, headers, (75, 185, 85, 95, 78, 78, 122, 135)):
        tree.heading(col, text=title)
        tree.column(col, width=width, anchor="w" if col in {"group", "label", "clause"} else "center")
    bar = ttk.Scrollbar(table, orient="vertical", command=tree.yview)
    tree.configure(yscrollcommand=bar.set)
    bar.pack(side="right", fill="y")
    tree.pack(fill="both", expand=True)

    box = ttk.LabelFrame(panes, text="分项明细", padding=5)
    panes.add(box, weight=2)
    detail = tk.Text(box, wrap="word", height=7, borderwidth=1, relief="sunken", state="disabled")
    scroll = ttk.Scrollbar(box, command=detail.yview)
    detail.configure(yscrollcommand=scroll.set)
    scroll.pack(side="right", fill="y")
    detail.pack(fill="both", expand=True)
    items = {item["id"]: item for item in report["items"]}
    for key, item in items.items():
        value = item.get("value_m")
        bounds = f"{display_score(item.get('min_score', 0))} ～ {display_score(item['max_score'])}"
        tree.insert("", "end", iid=key, values=(GROUP_NAMES[item["group"]], item["label"],
                    "" if value is None else f"{value:.3f}", _VALUE_SOURCES.get(item.get("value_source"), ""),
                    display_score(item["score"]), display_score(item["regulation_score"], regulation=True),
                    bounds, item["clause"]))

    def select_item(event=None):
        selection = tree.selection()
        if not selection:
            return
        item = items[selection[0]]
        reason, values = _item_details(report, item)
        lines = [f"{item['label']}    {item['clause']}    {report['assessment']}",
                 f"PPT 三位 {display_score(item['score'])}    "
                 f"规程计分 {display_score(item['regulation_score'], regulation=True)}"]
        if "mean_m" in item:
            lines.append(f"评分均值 {item['mean_m']:g} m    标准偏差 {item['sd_m']:g} m"
                         f"    满分界限 {item['upper_m']:g} m")
        if "ies_estimate" in values:
            estimate = values["ies_estimate"]
            if estimate["value_m"] is None:
                lines.append(f"本项采用录入实测值。IES 工程估算未评定（{estimate['reason']}）。")
            else:
                lines.append(f"本项采用录入实测值。IES 工程估算为 {estimate['value_m']:.3f} m。")
        if reason:
            lines.append(reason)
        if values:
            lines.append(json.dumps(values, indent=2, ensure_ascii=False))
        detail.configure(state="normal")
        detail.delete("1.0", "end")
        detail.insert("1.0", "\n".join(lines))
        detail.configure(state="disabled")

    tree.bind("<<TreeviewSelect>>", select_item)
    if items:
        first = next(iter(items))
        tree.selection_set(first)
        tree.focus(first)
        select_item()
    buttons = ttk.Frame(frame)
    buttons.pack(fill="x", pady=(8, 0))
    ttk.Button(buttons, text="关闭", command=dialog.destroy).pack(side="right")
    ttk.Button(buttons, text="导出结果…", command=on_export).pack(side="right", padx=6)
    return dialog


def show_log(parent, text, on_clear, on_details, on_export):
    """Open the report log and return the window and its content updater."""
    dialog = tk.Toplevel(parent)
    dialog.title("报告")
    dialog.geometry("300x200")
    dialog.minsize(250, 120)
    frame = ttk.Frame(dialog, padding=5)
    frame.pack(fill="both", expand=True)
    actions = ttk.Frame(frame)
    actions.pack(side="bottom", fill="x", pady=(4, 0))
    ttk.Button(actions, text="Clear", width=7, command=on_clear).pack(side="left")
    export = ttk.Button(actions, text="导出…", width=7, command=on_export)
    export.pack(side="right")
    details = ttk.Button(actions, text="评分明细", width=9, command=on_details)
    details.pack(side="right", padx=4)
    output = tk.Text(frame, wrap="word", borderwidth=1, relief="sunken", width=1, height=1)
    scrollbar = ttk.Scrollbar(frame, command=output.yview)
    output.configure(yscrollcommand=scrollbar.set)
    scrollbar.pack(side="right", fill="y")
    output.pack(fill="both", expand=True)

    def refresh(text, has_result):
        output.configure(state="normal")
        output.delete("1.0", "end")
        output.insert("1.0", text)
        output.see("end")
        output.configure(state="disabled")
        for button in (details, export):
            button.state(["!disabled" if has_result else "disabled"])

    refresh(text, False)
    return dialog, refresh
