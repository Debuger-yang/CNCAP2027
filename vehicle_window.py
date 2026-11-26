"""2027 desktop window patterned after the 260114 MainVehicle lamp editor."""
from concurrent.futures import ThreadPoolExecutor
from math import isfinite
from pathlib import Path
from queue import Empty, SimpleQueue
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from .engine import calculate
from .output import display_score, summaries, write_report
from .vehicle_dialogs import edit_inputs, show_log, show_results
from .vehicle_editors import edit_fields, view_ies
from .vehicle_project import ADB_SCENARIOS, LAMP_GROUPS, SLOTS, VehicleProject


MODES = ("C-NCAP: HB & LB", "C-NCAP: ADB")
MODELS = ("radial", "vertical", "horizontal")
DIMS = ("z_m", "x_m")


def slot_sign(slot):
    return -1 if slot.rstrip("0123456789").endswith("L") else 1


def lamp_side(slot):
    return "左" if slot_sign(slot) < 0 else "右"


def scene_name(slot, order=ADB_SCENARIOS):
    if not slot.startswith("ADB"):
        return ""
    kind, distance = order[int(slot[4:]) - 1].split("_")
    return f"{'对向' if kind == 'oncoming' else '跟随'} {distance} m"


class VehicleWindow:
    def __init__(self, root):
        self.root = root
        root.title("MainVehicle · C-NCAP 2027")
        root.geometry("640x480")
        root.minsize(600, 350)
        root.protocol("WM_DELETE_WINDOW", self.close)
        root.columnconfigure(0, weight=1)
        root.rowconfigure(2, weight=1)
        self.project = VehicleProject()
        # 260114 sums each lamp's directional I/r² along the road measurement lines.
        self.project.config["photometry"] = {"projection": "radial"}
        self.executor = ThreadPoolExecutor(max_workers=1)
        self.report = self.result_window = None
        self.log_window = self.log_refresh = None
        self.messages = []
        self.output_dir = self.config_path = None
        self.needs_adb_mapping = False
        self.busy = False
        self.version = 0
        self.mode = tk.StringVar(root, value=MODES[0])
        self.status = tk.StringVar(root)
        self.dimensions = {}
        self.group_defaults = {}
        self.dirty = set()
        self.collapsed = set()
        style = ttk.Style(root)
        style.configure("Lamp.TFrame", background="white")
        style.configure("Lamp.TLabel", background="white")
        style.configure("Lamp.TButton", padding=(2, 0))

        toolbar = ttk.Frame(root, padding=(3, 2))
        toolbar.grid(row=0, column=0, sticky="ew")
        self.run = ttk.Button(toolbar, text="measure", width=10, command=self.measure)
        self.run.pack(side="left", padx=(0, 5))
        self.save = ttk.Button(toolbar, text="report", width=10, command=self.open_log)
        self.save.pack(side="left", padx=(0, 5))
        self.selector = ttk.Combobox(toolbar, textvariable=self.mode, values=MODES, state="readonly", width=22)
        self.selector.pack(side="left")
        self.selector.bind("<<ComboboxSelected>>", self.change_mode)
        bar = ttk.Frame(root, padding=(2, 2))
        bar.grid(row=1, column=0, sticky="ew")
        ttk.Label(bar, text="VehicleWidget").pack(side="left")
        ttk.Button(bar, text="load Folder", command=self.load_folder).pack(side="left", padx=5)
        extra = ttk.Menubutton(bar, text="2027 项目")
        extra.pack(side="left")
        menu = tk.Menu(extra, tearoff=False)
        menu.add_command(label="打开整车配置…", command=self.open_project)
        menu.add_command(label="实测值与功能审核…", command=self.measurements)
        menu.add_command(label="项目和计算设置…", command=self.settings)
        menu.add_command(label="ADB 灯号与工况…", command=self.edit_adb_order)
        menu.add_separator()
        menu.add_command(label="评分明细…", command=self.details)
        menu.add_command(label="导出本次结果…", command=self.export)
        extra.configure(menu=menu)
        ttk.Label(bar, textvariable=self.status).pack(side="left", padx=6)

        border = ttk.Frame(root, relief="sunken", borderwidth=1)
        border.grid(row=2, column=0, sticky="nsew")
        border.columnconfigure(0, weight=1)
        border.rowconfigure(1, weight=1)
        header = ttk.Frame(border, style="Lamp.TFrame")
        header.grid(row=0, column=0, sticky="ew")
        self.column_sizes = (125, 113, 113)
        self.columns(header)
        for col, label in enumerate(("Lamp Name", "Distance to\nGround /m", "Distance to\nXZ Plane /m")):
            ttk.Label(header, text=label, anchor="center", justify="center",
                      style="Lamp.TLabel", relief="groove", padding=(0, 2)).grid(row=0, column=col, sticky="nsew")
        self.canvas = tk.Canvas(border, background="white", highlightthickness=0, takefocus=False)
        scroll = ttk.Scrollbar(border, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=scroll.set)
        self.canvas.grid(row=1, column=0, sticky="nsew")
        scroll.grid(row=0, rowspan=2, column=1, sticky="ns")
        self.body = ttk.Frame(self.canvas, style="Lamp.TFrame")
        self.columns(self.body)
        self.embedded = self.canvas.create_window(0, 0, anchor="nw", window=self.body)
        self.canvas.bind("<Configure>", lambda e: self.canvas.itemconfigure(self.embedded, width=e.width))
        self.body.bind("<Configure>", lambda _: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        root.bind("<MouseWheel>", lambda e: self.canvas.yview_scroll(-int(e.delta/120)*3, "units"))
        root.bind("<Control-o>", lambda _: self.open_project())
        root.bind("<F5>", lambda _: self.measure())
        self.render()

    def columns(self, widget):
        for i, width in enumerate(self.column_sizes):
            widget.columnconfigure(i, minsize=width)
        widget.columnconfigure(3, weight=1)

    def invalidate(self):
        self.version += 1
        self.report = None
        if self.result_window is not None and self.result_window.winfo_exists():
            self.result_window.destroy()
        self.result_window = None
        self.status.set("")
        self.refresh_log()

    def value_for(self, group, slots, key):
        values = {v for slot in slots if (v := self.project.lamp(slot).get(key)) is not None}
        if key == "x_m":
            values = {abs(v) for v in values}
        if len(values) > 1:
            return ""
        return f"{next(iter(values)):g}" if values else self.group_defaults.get((group, key), "0.75")

    def render(self):
        for widget in self.body.winfo_children():
            widget.destroy()
        self.dimensions.clear()
        self.dirty.clear()
        row = 0
        for name, slots in (("Uncategorized", ()), *LAMP_GROUPS):
            closed = name in self.collapsed
            heading = ttk.Label(self.body, text=(" ▸  " if closed else " ▾  ") + name, style="Lamp.TLabel")
            heading.grid(row=row, column=0, sticky="ew", ipady=2)
            heading.bind("<Button-1>", lambda _, n=name: self.toggle(n))
            for col, key in enumerate(DIMS, 1):
                val = tk.StringVar(self.root, self.value_for(name, slots, key))
                self.dimensions[name, key] = val
                field = ttk.Entry(self.body, textvariable=val, justify="center", width=9)
                field.grid(row=row, column=col, sticky="ew")
                val.trace_add("write", lambda *_, n=name, k=key: self.changed_dimension(n, k))
            row += 1
            if closed:
                continue
            if name == "Uncategorized":
                for path in self.project.unassigned:
                    path = Path(path)
                    ttk.Label(self.body, text=path.name, style="Lamp.TLabel", width=14).grid(row=row, column=0, sticky="e")
                    ttk.Button(self.body, text="View", command=lambda p=path: self.preview(p)).grid(row=row, column=1)
                    btn = ttk.Menubutton(self.body, text="分配至…")
                    choices = tk.Menu(btn, tearoff=False)
                    for slot in SLOTS:
                        choices.add_command(label=f"{slot}  {scene_name(slot, self.project.adb_order)}", command=lambda s=slot,p=path: self.assign(s, p, True))
                    btn.configure(menu=choices)
                    btn.grid(row=row, column=2)
                    ttk.Label(self.body, text=str(path), style="Lamp.TLabel", width=10).grid(row=row, column=3, sticky="ew")
                    row += 1
            for slot in slots:
                self.lamp_row(row, slot)
                row += 1

    def lamp_row(self, row, slot):
        spec = self.project.lamp(slot)
        filename = spec.get("ies")
        label = ttk.Label(self.body, text=slot, style="Lamp.TLabel", padding=(40, 0, 0))
        label.grid(row=row, column=0, sticky="ew")
        buttons = ttk.Frame(self.body, style="Lamp.TFrame")
        buttons.grid(row=row, column=1, columnspan=2, sticky="e")
        lamp = tk.Canvas(buttons, width=16, height=16, highlightthickness=0, background="white")
        lamp.create_oval(1, 1, 15, 15, fill="#269d49" if filename else "#ee1919", outline="#555555")
        lamp.pack(side="left", padx=2)
        view = ttk.Button(buttons, text="View", width=5, style="Lamp.TButton",
                          command=lambda: self.preview(self.project.base_dir / filename))
        view.pack(side="left", padx=1)
        if not filename:
            view.state(["disabled"])
        ttk.Button(buttons, text="Select", width=6, style="Lamp.TButton",
                   command=lambda: self.select(slot)).pack(side="left", padx=(1, 3))
        state = ttk.Label(self.body, text=(Path(filename).name if filename else "not Selected") +
                          (f"   ·   {scene_name(slot, self.project.adb_order)}" if slot.startswith("ADB") else ""),
                          style="Lamp.TLabel", anchor="center", width=12)
        state.grid(row=row, column=3, sticky="ew", ipady=3)
        for target in (state, label, lamp):
            target.bind("<Double-Button-1>", lambda _, s=slot: self.geometry(s))
            target.bind("<Button-3>", lambda e, s=slot: self.lamp_menu(e, s))

    def changed_dimension(self, group, key):
        self.dirty.add((group, key))
        self.invalidate()

    def apply_dimensions(self):
        updates = {}
        for group, key in self.dirty:
            raw = self.dimensions[group, key].get().strip()
            try:
                value = float(raw)
                if not isfinite(value) or value < 0 or (key == "z_m" and value == 0):
                    raise ValueError()
            except ValueError:
                messagebox.showerror(group, "请填写有效的距离（m），灯具高度须大于零。", parent=self.root)
                return False
            updates[group, key] = value
        for (group, key), value in updates.items():
            self.group_defaults[group, key] = f"{value:g}"
            for slot in dict(LAMP_GROUPS).get(group, ()):
                spec = self.project.lamp(slot)
                if spec.get("ies"):
                    self.project.set_lamp(slot, {**spec, key: value * slot_sign(slot) if key == "x_m" else value})
        self.dirty.clear()
        return True

    def toggle(self, name):
        if self.apply_dimensions():
            self.collapsed.symmetric_difference_update([name])
            self.render()

    def change_mode(self, event=None):
        if not self.apply_dimensions():
            self.mode.set(MODES[int(self.project.has_adb)])
            return
        self.project.has_adb = self.mode.get() == MODES[1]
        self.invalidate()

    def select(self, slot):
        filename = filedialog.askopenfilename(parent=self.root, title=f"Select · {slot}",
                                              filetypes=[("IES 配光", "*.ies")])
        if filename:
            self.assign(slot, Path(filename))

    def defaults_for(self, slot, group, *, fresh=False):
        defaults = {"y_m": 0.0, "yaw_deg": 0.0, "pitch_deg": 0.0}
        for key in DIMS:
            value = "0.75" if fresh else self.dimensions[group, key].get()
            if value:
                defaults[key] = float(value) * (slot_sign(slot) if key == "x_m" else 1)
        return defaults

    def assign(self, slot, path, unassigned=False):
        if not self.apply_dimensions():
            return
        group = next(name for name, slots in LAMP_GROUPS if slot in slots)
        defaults = self.defaults_for(slot, "Uncategorized" if unassigned else group)
        self.project.set_lamp(slot, {**defaults, **self.project.lamp(slot), "ies": str(path)})
        self.invalidate()
        self.render()

    def lamp_menu(self, event, slot):
        menu = tk.Menu(self.root, tearoff=False)
        menu.add_command(label="选择配光…", command=lambda: self.select(slot))
        menu.add_command(label="单灯安装参数…", command=lambda: self.geometry(slot))
        if self.project.lamp(slot).get("ies"):
            menu.add_command(label="移除此分配", command=lambda: self.clear_slot(slot))
        menu.tk_popup(event.x_root, event.y_root)

    def clear_slot(self, slot):
        if self.apply_dimensions():
            self.project.set_lamp(slot, {})
            self.invalidate()
            self.render()

    def geometry(self, slot):
        if not self.apply_dimensions():
            return
        spec = self.project.lamp(slot)
        if not spec.get("ies"):
            self.select(slot)
            return
        defs = [(key, label, spec.get(key, 0 if key in ("y_m","yaw_deg","pitch_deg") else None), (), float)
                for key,label in (("x_m","横向 x (m)，向右为正"), ("y_m","纵向 y (m)，向前为正"),
                                  ("z_m","高度 z (m)"), ("yaw_deg","右偏 (°)"), ("pitch_deg","上仰 (°)"))]
        values = edit_fields(self.root, f"{slot} · {lamp_side(slot)}灯安装参数", defs)
        if values is not None:
            if any(v is None for v in values.values()) or values["z_m"] <= 0:
                messagebox.showerror(slot, "请填写全部安装参数，且高度须大于零。", parent=self.root)
                return
            self.project.set_lamp(slot, {**spec, **values})
            self.invalidate()
            self.render()

    def preview(self, path):
        try:
            view_ies(self.root, path)
        except (ValueError, OSError) as exc:
            messagebox.showerror("View", str(exc), parent=self.root)

    def open_project(self):
        filename = filedialog.askopenfilename(parent=self.root, title="打开整车配置", filetypes=[("整车 JSON","*.json")])
        if not filename:
            return
        try:
            project = VehicleProject.from_file(filename)
        except (ValueError, OSError, TypeError) as exc:
            messagebox.showerror("打开整车配置", str(exc), parent=self.root)
            return
        self.project = project
        self.config_path = Path(filename).resolve()
        self.needs_adb_mapping = False
        self.output_dir = None
        self.group_defaults.clear()
        self.invalidate()
        self.mode.set(MODES[int(self.project.has_adb)])
        self.render()
        self.status.set(Path(filename).name)
        self.log(f"打开项目 {filename}")

    def load_folder(self):
        if not self.apply_dimensions():
            return
        selected = filedialog.askdirectory(parent=self.root)
        if not selected:
            return
        folder = Path(selected).resolve()
        # Switching a project folder must not export the previous vehicle's scores
        # under the new folder. Keep only the current mode and calculation settings.
        switching = self.config_path is not None or (self.output_dir is not None and self.output_dir != folder)
        target = self.project
        if switching:
            target = VehicleProject()
            target.has_adb = self.project.has_adb
            previous = self.project.config.get("photometry", {})
            target.config["photometry"] = {k: previous[k] for k in
                ("projection", "projection_1_5m", "step_m", "max_distance_m", "half_width_m") if k in previous}
            target.config["photometry"].setdefault("projection", "radial")
        existing = {s for s in SLOTS if target.lamp(s).get("ies")}
        try:
            target.load_folder(folder)
            for group, slots in LAMP_GROUPS:
                for slot in slots:
                    spec = target.lamp(slot)
                    if slot not in existing and spec.get("ies"):
                        target.set_lamp(slot, {**self.defaults_for(slot, group, fresh=switching), **spec})
        except (ValueError, OSError) as exc:
            messagebox.showerror("load Folder", str(exc), parent=self.root)
            return
        self.needs_adb_mapping = ((self.needs_adb_mapping and not switching) or any(
            slot.startswith("ADB") and slot not in existing and target.lamp(slot).get("ies") for slot in SLOTS
        ))
        if switching:
            self.group_defaults.clear()
        self.project = target
        self.config_path = None
        self.invalidate()
        self.render()
        self.output_dir = folder
        self.status.set(f"待分配 {len(self.project.unassigned)} 个文件")
        self.log(f"载入配光文件夹 {selected}")

    def measurements(self):
        if self.apply_dimensions():
            values = edit_inputs(self.root, self.project.config)
            if values is not None:
                self.project.config = values
                self.invalidate()
                self.status.set("实测输入已更新")

    def edit_adb_order(self):
        if not self.apply_dimensions():
            return False
        labels = {scene: scene_name(f"ADBL{i}") for i, scene in enumerate(ADB_SCENARIOS, 1)}
        names = {v: k for k, v in labels.items()}
        order = self.project.adb_order
        while True:
            rows = [(str(i), f"line{i} · ADBL{i} / ADBR{i}", labels[name], tuple(names), str)
                    for i, name in enumerate(order, 1)]
            fields = edit_fields(self.root, "ADB 灯号与工况 · 对向 50 m 用于眩光", rows)
            if fields is None:
                return False
            order = tuple(names[fields[str(i)]] for i in range(1, 7))
            if len(set(order)) == len(ADB_SCENARIOS):
                break
            messagebox.showerror("ADB 灯号与工况", "六个灯号需要分别对应六种工况。", parent=self.root)
        self.project.set_adb_order(order)
        self.invalidate()
        self.needs_adb_mapping = False
        self.render()
        self.log("ADB 工况：" + "、".join(f"line{i} {labels[name]}" for i, name in enumerate(order, 1)))
        return True

    def settings(self):
        if not self.apply_dimensions():
            return False
        config = self.project.config
        opt = config.get("photometry", {})
        defs = [
            ("project", "车型 / 项目", config.get("project",""), (), str),
            ("projection", "路照接收面 (radial 按 2024 测基础近光)", opt.get("projection"), MODELS, str),
            ("projection_1_5m", "ADB 1.5 m 接收面", opt.get("projection_1_5m"), ("", "radial", "vertical"), str),
            ("step_m", "ADB / 非径向近光扫描步长 (m)", opt.get("step_m",.25), (), float),
            ("max_distance_m", "ADB / 非径向近光最远搜索距离 (m)", opt.get("max_distance_m",350), (), float),
            ("half_width_m", "非径向近光横向搜索半宽 (m)", opt.get("half_width_m",80), (), float)]
        result = edit_fields(self.root, "2027 · 项目和计算设置", defs)
        if result is None:
            return False
        config["project"] = result.pop("project") or ""
        opt = config.setdefault("photometry", {})
        for key, value in result.items():
            if value is None:
                opt.pop(key, None)
            else:
                opt[key] = value
        self.invalidate()
        return True

    def measure(self):
        if self.busy or not self.apply_dimensions():
            return
        if self.project.has_adb and self.needs_adb_mapping and not self.edit_adb_order():
            return
        try:
            config, base = self.project.build()
            optics = config.get("photometry", {})
            if (optics.get("low_beam") or optics.get("adb_scenarios")) and not optics.get("projection"):
                if not self.settings():
                    return
                config, base = self.project.build()
            if not any(config.get(k) for k in ("measurements","evidence")) and not any(
                k in config.get("photometry", {}) for k in ("low_beam","high_beam","adb_scenarios")
            ):
                messagebox.showinfo("measure", "请先选择左右灯配光，或在「2027 项目」中录入实测值或打开整车配置。", parent=self.root)
                return
        except (TypeError, ValueError, OSError) as exc:
            messagebox.showerror("measure", str(exc), parent=self.root)
            return
        self.invalidate()
        version = self.version
        self.busy = True
        self.run.state(["disabled"])
        self.status.set("正在测量…")
        events = SimpleQueue()
        future = self.executor.submit(calculate, config, base, progress=events.put)

        def finish():
            while True:
                try:
                    event = events.get_nowait()
                except Empty:
                    break
                if version == self.version:
                    self.status.set(event)
                    self.log(event)
            if not future.done():
                self.root.after(75, finish)
                return
            self.busy = False
            self.run.state(["!disabled"])
            if version != self.version:
                self.status.set("输入已更改，请重新测量")
                return
            try:
                self.report = future.result()
            except (TypeError, ValueError, OSError, ArithmeticError) as exc:
                self.status.set("测量未完成")
                self.log(str(exc))
                messagebox.showerror("measure", str(exc), parent=self.root)
                return
            self.status.set("测量完成")
            lines = [f"测量完成 · {self.report['assessment']} · 未评定 {len(self.report['missing'])} 项"]
            for label, ppt, regulation in summaries(self.report):
                lines.append(f"{label}    PPT 三位 {display_score(ppt)}    "
                             f"规程计分 {display_score(regulation, regulation=True)}")
            self.log("\n".join(lines))
            if self.output_dir is not None:
                try:
                    path = self.auto_report()
                    self.status.set("已生成 " + path.name)
                    self.log(f"评分报告 {path}")
                except (OSError, ValueError) as exc:
                    self.log(f"自动导出失败：{exc}")
                    messagebox.showerror("report", f"评分已完成。报告保存失败，可从 report 另行导出。\n{exc}", parent=self.root)
            else:
                self.details()
        self.root.after(70, finish)

    def auto_report(self):
        suffix = "withADB" if self.project.has_adb else "withoutADB"
        index = 1
        while True:
            path = self.output_dir / f"cncap2027_{suffix}_{index}.xlsx"
            try:
                with path.open("xb"):
                    pass
            except FileExistsError:
                index += 1
                continue
            break
        try:
            write_report(self.report, path)
        except (OSError, ValueError):
            path.unlink(missing_ok=True)
            raise
        return path

    def refresh_log(self):
        if self.log_window is not None and self.log_window.winfo_exists():
            self.log_refresh("\n".join(self.messages), self.report is not None)

    def log(self, line):
        self.messages.append(line)
        self.refresh_log()

    def clear_log(self):
        self.messages.clear()
        self.refresh_log()

    def open_log(self):
        if self.log_window is None or not self.log_window.winfo_exists():
            self.log_window, self.log_refresh = show_log(self.root, "", self.clear_log, self.details, self.export)
        self.refresh_log()
        self.log_window.deiconify()
        self.log_window.lift()

    def details(self):
        if self.report is None:
            messagebox.showinfo("measure", "请先点击 measure 计算评分。", parent=self.root)
        elif self.result_window is not None and self.result_window.winfo_exists():
            self.result_window.deiconify()
            self.result_window.lift()
        else:
            self.result_window = show_results(self.root, self.report, self.export)

    def export(self):
        if self.report is None:
            messagebox.showinfo("report", "请先点击 measure 计算评分。", parent=self.root)
            return
        name = filedialog.asksaveasfilename(parent=self.root, title="report", defaultextension=".xlsx",
                    initialfile=f"cncap2027_{'withADB' if self.project.has_adb else 'withoutADB'}_1.xlsx",
                    filetypes=[("Excel 评分表","*.xlsx"), ("JSON 完整结果","*.json")])
        if name:
            try:
                if self.config_path is not None and Path(name).resolve() == self.config_path:
                    raise ValueError("请选择与输入配置不同的输出文件")
                write_report(self.report, name)
                self.log(f"评分报告 {name}")
                self.status.set("已导出 " + Path(name).name)
            except (OSError, ValueError) as exc:
                messagebox.showerror("report", str(exc), parent=self.root)

    def close(self):
        self.root.destroy()
        self.executor.shutdown(wait=False, cancel_futures=True)


def run_desktop():
    root = tk.Tk()
    VehicleWindow(root)
    root.mainloop()
