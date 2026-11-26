"""Absolute cd from a measured LM-63 Type A forward headlamp distribution.

Vehicle frame in metres: x right, y forward, z up. H positive right and V
positive up match the archived LucidShape Type A exports in this workspace.
The IES file is the complete distribution of one lamp assembly. Lamp count
and lumen metadata do not scale the cd matrix. No additional mirror, aim
or flux adjustment is implicit.
"""
from __future__ import annotations

from array import array
from bisect import bisect_right
from dataclasses import dataclass, field
import hashlib
import math
from pathlib import Path
import re


class OutOfDomain(ValueError):
    """The requested direction is outside the measured angular grid."""


def _finite(value: float, label: str) -> float:
    if not math.isfinite(value):
        raise ValueError(f"{label} must be finite")
    return value


def _angular_bracket(axis: tuple[float, ...], angle: float, name: str):
    _finite(angle, name)
    # Permit only floating-point roundoff on an exact end point.
    if angle < axis[0] - 1e-10 or angle > axis[-1] + 1e-10:
        raise OutOfDomain(
            f"{name}={angle:.6f} deg outside [{axis[0]:g},{axis[-1]:g}] deg"
        )
    angle = min(axis[-1], max(axis[0], angle))
    left = min(bisect_right(axis, angle) - 1, len(axis) - 2)
    return left, (angle - axis[left]) / (axis[left + 1] - axis[left])


@dataclass(frozen=True)
class IESGrid:
    source: Path
    sha256: str
    horizontal: tuple[float, ...]
    vertical: tuple[float, ...]
    values_cd: array = field(repr=False)
    multiplier: float

    @classmethod
    def load(cls, path: str | Path) -> IESGrid:
        """Read forward Type A and TILT=NONE. Only measured angles are used."""
        source = Path(path).resolve()
        sha = hashlib.sha256()
        with source.open("rb") as file:
            def lines():
                for line in file:
                    sha.update(line)
                    yield line

            data = lines()
            for line in data:
                match = re.fullmatch(rb"\s*TILT\s*=\s*(.*?)\s*", line, re.I)
                if match:
                    if match.group(1).upper() != b"NONE":
                        raise ValueError(f"{source.name}: only TILT=NONE is supported")
                    break
            else:
                raise ValueError(f"{source.name}: missing IES TILT header")

            # Stream the matrix to support 0.01 degree grids from a lab.
            tokens = (token for line in data for token in line.replace(b",", b" ").split())

            def number() -> float:
                try:
                    return _finite(float(next(tokens)), "IES data")
                except StopIteration:
                    raise ValueError(f"{source.name}: truncated IES numeric data") from None

            header = [number() for _ in range(13)]
            if header[5] != 3:
                raise ValueError(f"{source.name}: photometric type {header[5]:g} unsupported (requires Type A=3)")
            nv, nh = int(header[3]), int(header[4])
            if nv != header[3] or nh != header[4] or min(nh, nv) < 2:
                raise ValueError("Both IES axes require at least two measured angles (integer counts)")
            multiplier = header[2]
            if multiplier <= 0:
                raise ValueError("IES candela multiplier must be positive")
            if header[10:12] != [1.0, 1.0]:
                raise ValueError("Only unity IES ballast / photometric factors are supported")

            vertical = tuple(number() for _ in range(nv))
            horizontal = tuple(number() for _ in range(nh))
            for axis in (vertical, horizontal):
                if any(b <= a for a, b in zip(axis, axis[1:])):
                    raise ValueError("IES angular axes must be strictly increasing")
            if vertical[0] < -90 or vertical[-1] > 90 or horizontal[0] < -180 or horizontal[-1] > 180:
                raise ValueError("Unsupported Type A angular conventions")

            values = array("d")
            for _ in range(nv * nh):
                value = _finite(number() * multiplier, "scaled candela")
                if value < 0:
                    raise ValueError("IES contains negative candela")
                values.append(value)
            if next(tokens, None) is not None:
                raise ValueError(f"{source.name}: unexpected data after the candela matrix")
        return cls(source, sha.hexdigest(), horizontal, vertical, values, multiplier)

    def candela(self, h_deg: float, v_deg: float) -> float:
        """Bilinear interpolation on the measured H,V grid, in cd."""
        ih, th = _angular_bracket(self.horizontal, h_deg, "H")
        iv, tv = _angular_bracket(self.vertical, v_deg, "V")
        stride = len(self.vertical)
        i = ih * stride + iv
        a = self.values_cd
        left = (1 - tv) * a[i] + tv * a[i + 1]
        right = (1 - tv) * a[i + stride] + tv * a[i + stride + 1]
        return (1 - th) * left + th * right

    def metadata(self) -> dict:
        h, v = self.horizontal, self.vertical
        max_step = max(b - a for axis in (h, v) for a, b in zip(axis, axis[1:]))
        return {
            "path": str(self.source), "sha256": self.sha256,
            "photometric_type": "A", "candela_multiplier": self.multiplier,
            "horizontal_deg": [h[0], h[-1]], "vertical_deg": [v[0], v[-1]],
            "horizontal_count": len(h), "vertical_count": len(v),
            "max_step_deg": max_step,
            "covers_s2_3_2_2_angles": h[0] <= -60 and h[-1] >= 60 and v[0] <= -7 and v[-1] >= 6,
            "sampling_at_most_0_01_deg": max_step <= 0.01 + 1e-10,
        }


@dataclass(frozen=True)
class Lamp:
    """One distribution with optical centre and additional aim relative to its IES.

    Rotate up by pitch, then right by yaw. Roll is zero. File aim is retained
    when yaw=pitch=0. Supply each actual lamp, including its own measured
    asymmetry. The point-source far-field approximation is used.
    """
    grid: IESGrid
    x_m: float
    y_m: float
    z_m: float
    yaw_deg: float = 0.0
    pitch_deg: float = 0.0
    _rotation: tuple = field(init=False, repr=False, compare=False)

    def __post_init__(self):
        for value in (self.x_m, self.y_m, self.z_m, self.yaw_deg, self.pitch_deg):
            _finite(value, "lamp geometry")
        yaw = math.radians(self.yaw_deg)
        pitch = math.radians(self.pitch_deg)
        object.__setattr__(self, "_rotation", (
            math.sin(yaw), math.cos(yaw), math.sin(pitch), math.cos(pitch),
        ))

    def _ray(self, x: float, y: float, z: float):
        dx, dy, dz = x - self.x_m, y - self.y_m, z - self.z_m
        r = math.hypot(dx, dy, dz)
        if not math.isfinite(r) or r <= 0:
            raise ValueError("Target needs a finite nonzero distance from the lamp")
        sy, cy, sp, cp = self._rotation
        right = cy * dx - sy * dy
        forward_horizontal = sy * dx + cy * dy
        forward = cp * forward_horizontal + sp * dz
        up = cp * dz - sp * forward_horizontal
        h = math.degrees(math.atan2(right, forward))
        v = math.degrees(math.atan2(up, math.hypot(right, forward)))
        return self.grid.candela(h, v), r, dy, dz


class LampGroup:
    """A vehicle's lights active in one condition. Sums per-lamp contributions."""
    def __init__(self, lamps):
        self.lamps = tuple(lamps)
        if not self.lamps:
            raise ValueError("At least one lamp is needed")

    def intensity(self, x: float, y: float, z: float) -> float:
        """Sum of candela in the individual lamp-to-target directions."""
        return sum(lamp._ray(x, y, z)[0] for lamp in self.lamps)

    def radial_lux(self, x: float, y: float, z: float) -> float:
        """Sum I/r². Receiver normal is taken along each lamp-to-target ray."""
        return sum(cd / r**2 for cd, r, _, _ in (lamp._ray(x, y, z) for lamp in self.lamps))

    def radial_lux_260114(self, x: float, y: float, z: float) -> float:
        """Sum radial illuminance as in 260114, with zero contribution outside each lamp's IES."""
        total = 0.0
        for lamp in self.lamps:
            try:
                cd, distance, _, _ = lamp._ray(x, y, z)
            except OutOfDomain:
                continue
            total += cd / distance**2
        return total

    def high_beam_intensity(self, x: float, y: float, z: float) -> float:
        """260114 five-point method: convert combined lux to cd at the left lamp's distance."""
        left = self.lamps[0]  # load_lamp_pair orders the pair left, right.
        distance = math.hypot(x - left.x_m, y - left.y_m, z - left.z_m)
        return self.radial_lux_260114(x, y, z) * distance**2

    def vertical_lux(self, x: float, y: float, z: float) -> float:
        """Illuminance on a single-sided vertical sensor facing the vehicle (-y)."""
        return sum(cd / r**2 * max(0.0, dy / r)
                   for cd, r, dy, _ in (lamp._ray(x, y, z) for lamp in self.lamps))

    def horizontal_lux(self, x: float, y: float, z: float) -> float:
        """Upward-facing (+z), single-sided horizontal sensor, E=I*cos(theta)/r²."""
        return sum(cd / r**2 * max(0.0, -dz / r)
                   for cd, r, _, dz in (lamp._ray(x, y, z) for lamp in self.lamps))
