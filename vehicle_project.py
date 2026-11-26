"""Editable vehicle and lamp assignments for the desktop application."""
from copy import deepcopy
from pathlib import Path
import re

from . import scoring
from .engine import load_config, mapping


ADB_SCENARIOS = scoring.SCENARIOS
LAMP_GROUPS = (
    ("Pos Lamps", ("PosL", "PosR")),
    ("Low Beams", ("LBL", "LBR")),
    ("High Beams", ("HBL", "HBR")),
    ("Adaptive Beams", tuple(f"ADB{side}{i}" for side in "LR" for i in range(1, 7))),
)
SLOTS = tuple(slot for _, slots in LAMP_GROUPS for slot in slots)
_BY_STEM = {slot.casefold(): slot for slot in SLOTS}
_BEAM_PATHS = {
    "LBL": ("low_beam", "left"), "LBR": ("low_beam", "right"),
    "HBL": ("high_beam", "left"), "HBR": ("high_beam", "right"),
}
_ADB_SLOTS = {
    f"ADB{abbr}{i + 1}": (i, side)
    for i in range(len(ADB_SCENARIOS)) for abbr, side in (("L", "left"), ("R", "right"))
}


def _folder_slot(file):
    stem = file.stem.casefold()
    if stem in _BY_STEM:
        return _BY_STEM[stem]
    # The legacy loader searches the basename, then the portion after its beam marker.
    # Keep ambiguous beams/sides available for an explicit assignment in the tree.
    markers = list(re.finditer(r"lb|hb|line[1-6](?![0-9])", stem))
    if len({match.group() for match in markers}) != 1:
        return None
    sides = set(re.findall(r"_([lr])(?=[._])", file.name.casefold()[markers[0].end():]))
    if len(sides) != 1:
        return None
    side = sides.pop().upper()
    marker = markers[0].group()
    if marker.startswith("line"):
        return f"ADB{side}{marker[-1]}"
    return marker.upper() + side


class VehicleProject:
    def __init__(self):
        self.config = {"has_adb": False}
        self.base_dir = Path.cwd()
        self.unassigned = []
        # Position lamp IES are references. The engine expects measured area in cm².
        self._position_lamps = {}
        self._exclude_adb = True
        self._adb_order = ADB_SCENARIOS

    @classmethod
    def from_file(cls, path):
        path = Path(path).resolve()
        data = mapping(load_config(path), "配置",
                       {"project", "has_adb", "photometry", "measurements", "evidence"})
        if type(data.get("has_adb")) is not bool:
            raise ValueError("请指定 has_adb 为 true 或 false")
        for field in ("measurements", "evidence", "photometry"):
            if field in data:
                mapping(data[field], field)
        measurements = data.get("measurements", {})
        for group, rules in (("low_beam", scoring.LOW_RULES), ("high_beam", scoring.HIGH_RULES)):
            if group in measurements:
                scoring._measurements(mapping(measurements[group], group), rules, group)
        scenes = mapping(measurements.get("adb_scenarios", {}), "adb_scenarios", ADB_SCENARIOS)
        for scene, values in scenes.items():
            scoring._measurements(mapping(values, scene), scoring.ADB_RULES, scene)
        # Validate field types without changing the user's selections or discarding ADB values.
        scoring._evidence_inputs(data.get("evidence", {}), has_adb=True)
        instance = cls()
        instance.config = data
        instance.base_dir = path.parent
        # A supplied project must reach the engine intact, including conflicting ADB inputs.
        instance._exclude_adb = False
        # The tree renders dimensions and filenames immediately, before a calculation.
        for slot in (*_BEAM_PATHS, *_ADB_SLOTS):
            spec = instance.lamp(slot)
            if "ies" in spec and (not isinstance(spec["ies"], str) or not spec["ies"].strip()):
                raise ValueError(f"{slot}.ies 需要文件路径")
            for key in ("x_m", "y_m", "z_m", "pitch_deg", "yaw_deg"):
                if key in spec:
                    scoring._number(spec[key], f"{slot}.{key}", signed=True)
        return instance

    @property
    def has_adb(self):
        return self.config["has_adb"]

    @has_adb.setter
    def has_adb(self, enabled):
        if type(enabled) is not bool:
            raise ValueError("配备 ADB 需要布尔值")
        if enabled != self.has_adb:
            self._exclude_adb = not enabled
            self.config["has_adb"] = enabled

    @property
    def adb_order(self):
        """Physical scene names for lamp pairs numbered 1 through 6 in the tree."""
        return self._adb_order

    def set_adb_order(self, order):
        """Keep each IES on its numbered tree slot while changing its physical scene."""
        new_order = tuple(order)
        if (len(new_order) != len(ADB_SCENARIOS) or
                not all(isinstance(scene, str) for scene in new_order) or
                set(new_order) != set(ADB_SCENARIOS)):
            raise ValueError("ADB 编号须分别对应六个不同的物理工况")
        if new_order == self._adb_order:
            return
        optics = self.config.get("photometry", {})
        if "adb_scenarios" in optics:
            rename = dict(zip(self._adb_order, new_order))
            scenes = mapping(optics["adb_scenarios"], "photometry.adb_scenarios", ADB_SCENARIOS)
            optics["adb_scenarios"] = {rename[key]: settings for key, settings in scenes.items()}
        self._adb_order = new_order

    def _lamp_path(self, slot):
        if slot in _ADB_SLOTS:
            index, side = _ADB_SLOTS[slot]
            return ("adb_scenarios", self._adb_order[index], side)
        return _BEAM_PATHS[slot]

    def lamp(self, slot):
        """Return a copy of this lamp's editable engine fields."""
        if slot in ("PosL", "PosR"):
            return self._position_lamps.get(slot, {}).copy()
        branch = self.config.get("photometry", {})
        for key in self._lamp_path(slot):
            branch = mapping(branch.get(key, {}), f"{slot}.{key}")
        return branch.copy()

    def _ies_path(self, spec):
        filename = spec.get("ies")
        return (self.base_dir / filename).resolve() if isinstance(filename, str) and filename else None

    def set_lamp(self, slot, fields):
        """Replace a slot; pass {} to clear it. Keep loose IES available for reassignment."""
        old_path = self._ies_path(self.lamp(slot))
        fields = dict(fields)
        if slot in ("PosL", "PosR"):
            if fields:
                self._position_lamps[slot] = fields
            else:
                self._position_lamps.pop(slot, None)
        else:
            branch = self.config
            parents = []
            *keys, side = ("photometry", *self._lamp_path(slot))
            for key in keys:
                if not fields and key not in branch:
                    return
                parents.append((branch, key))
                branch = branch.setdefault(key, {})
            if fields:
                branch[side] = fields
            else:
                branch.pop(side, None)
                for parent, key in reversed(parents):
                    if parent[key]:
                        break
                    del parent[key]
        new_path = self._ies_path(fields)
        if new_path in self.unassigned:
            self.unassigned.remove(new_path)
        if old_path and old_path != new_path and old_path not in self.unassigned:
            if not any(self._ies_path(self.lamp(other)) == old_path for other in SLOTS):
                self.unassigned.append(old_path)

    def load_folder(self, folder):
        """Import identifiable lamps without overwriting an assignment or choosing a duplicate."""
        folder = Path(folder).resolve()
        files = sorted((p for p in folder.iterdir() if p.suffix.casefold() == ".ies" and p.is_file()),
                       key=lambda p: p.name.casefold())
        if not files:
            raise ValueError("该文件夹中没有 IES 文件")
        assigned = {self._ies_path(self.lamp(slot)) for slot in SLOTS}
        candidates = {}
        for file in files:
            candidates.setdefault(_folder_slot(file), []).append(file)
        for slot, matches in candidates.items():
            if slot and len(matches) == 1:
                fields = self.lamp(slot)
                if not fields.get("ies"):
                    file = matches[0]
                    self.set_lamp(slot, {**fields, "ies": str(file)})
                    assigned.add(file)
        for file in files:
            if file not in assigned and file not in self.unassigned:
                self.unassigned.append(file)

    def build(self):
        """Take an isolated worker snapshot. Keep inactive ADB inputs for later edits."""
        data = deepcopy(self.config)
        if self._exclude_adb and not self.has_adb:
            data.get("photometry", {}).pop("adb_scenarios", None)
            data.get("measurements", {}).pop("adb_scenarios", None)
            evidence = data.get("evidence", {})
            for key in scoring._ADB_EVIDENCE:
                evidence.pop(key, None)
        return data, self.base_dir
