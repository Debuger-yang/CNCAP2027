"""Small editors for the MainVehicle work surface."""
import math
import tkinter as tk
from tkinter import messagebox, ttk

from .photometry import IESGrid


def edit_fields(parent, title, definitions):
    """Return typed values for (key, label, value, choices, converter) rows."""
    win = tk.Toplevel(parent)
    win.withdraw()
    win.title(title)
    win.transient(parent)
    win.resizable(False, False)
    pane = ttk.Frame(win, padding=12)
    pane.pack(fill="both", expand=True)
    entries = {}
    result = None
    for i, (key, label, current, choices, _) in enumerate(definitions):
        ttk.Label(pane, text=label).grid(row=i, column=0, sticky="w", padx=(0, 14), pady=4)
        var = tk.StringVar(win, value="" if current is None else current)
        entries[key] = var
        control = (ttk.Combobox(pane, textvariable=var, values=choices, state="readonly", width=26)
                   if choices else ttk.Entry(pane, textvariable=var, width=29))
        control.grid(row=i, column=1, sticky="ew", pady=4)
        if i == 0:
            first = control

    def accept(event=None):
        nonlocal result
        data = {}
        for key, label, _, _, convert in definitions:
            raw = entries[key].get().strip()
            try:
                value = convert(raw) if raw else None
                if isinstance(value, float) and not math.isfinite(value):
                    raise ValueError()
            except ValueError:
                messagebox.showerror(title, f"请检查{label}。", parent=win)
                return
            data[key] = value
        result = data
        win.destroy()

    buttons = ttk.Frame(pane)
    buttons.grid(row=len(definitions), columnspan=2, sticky="e", pady=(10, 0))
    ttk.Button(buttons, text="确定", command=accept).pack(side="left", padx=3)
    ttk.Button(buttons, text="取消", command=win.destroy).pack(side="left")
    win.bind("<Return>", accept)
    win.bind("<Escape>", lambda _: win.destroy())
    win.deiconify()
    win.grab_set()
    first.focus_set()
    parent.wait_window(win)
    return result


def view_ies(parent, path):
    grid = IESGrid.load(path)
    win = tk.Toplevel(parent)
    win.title(f"View · {path.name}")
    pane = ttk.Frame(win, padding=10)
    pane.pack(fill="both", expand=True)
    h0, h1 = grid.horizontal[0], grid.horizontal[-1]
    v0, v1 = grid.vertical[0], grid.vertical[-1]
    peak = max(grid.values_cd)
    ttk.Label(pane, text=f"{path.name}    Type A    {len(grid.horizontal)} × {len(grid.vertical)}"
                         f"    最大光强 {peak:,.1f} cd").pack(anchor="w")
    width, height = 340, 180
    # Each pixel is evaluated on the measured angular axes; upper V is at the top.
    img = tk.PhotoImage(master=win, width=width, height=height)
    colors = ["#%02x%02x%02x" % (min(255, i*3), max(0, min(255, i*3-150)),
                                  max(0, min(255, i*4-650))) for i in range(256)]
    pixels = []
    for j in range(height):
        v = v1 - j * (v1-v0) / (height-1)
        row = (grid.candela(h0 + i*(h1-h0)/(width-1), v) for i in range(width))
        pixels.append("{" + " ".join(colors[min(255, int(255 * (cd/peak)**.5))] if peak else colors[0]
                                     for cd in row) + "}")
    img.put(" ".join(pixels))
    pic = ttk.Label(pane, image=img)
    pic.image = img
    pic.pack(pady=6)
    ttk.Label(pane, text=f"H 从左至右 {h0:g}° 至 {h1:g}°    V 从上至下 {v1:g}° 至 {v0:g}°").pack()
    readout = tk.StringVar(win, value="移动鼠标查看 H、V 和光强 (cd)")
    ttk.Label(pane, textvariable=readout).pack()
    def sample(event):
        h = h0 + min(1, max(0, event.x/(width-1))) * (h1-h0)
        v = v1 - min(1, max(0, event.y/(height-1))) * (v1-v0)
        readout.set(f"H {h:.2f}°     V {v:.2f}°     {grid.candela(h, v):,.1f} cd")
    pic.bind("<Motion>", sample)
    return win
