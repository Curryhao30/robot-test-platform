"""Robot Profile：平台配置中心。

单一事实来源：orchestrator（判定阈值）与 C++ Agent（控制环参数）读取同一份 YAML。
P0 字段：轴数 / 控制周期 / 各轴限位·限速·限加加速度 / 精度阈值。
"""
from __future__ import annotations

from dataclasses import dataclass
import pathlib
from typing import Any

import yaml

_PROFILE_ROOT = pathlib.Path(__file__).resolve().parents[2] / "robot_profiles"


@dataclass(frozen=True)
class JointLimits:
    min_pos: float
    max_pos: float
    max_vel: float
    max_acc: float
    max_jerk: float

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "JointLimits":
        limits = d["limits"] if "limits" in d else d
        return cls(
            min_pos=float(limits["position"][0]),
            max_pos=float(limits["position"][1]),
            max_vel=float(limits["velocity"]),
            max_acc=float(limits["acceleration"]),
            max_jerk=float(limits["jerk"]),
        )


@dataclass(frozen=True)
class RobotProfile:
    name: str
    manufacturer: str
    model: str
    dof: int
    cycle_hz: int
    joints: tuple[JointLimits, ...]
    position_tolerance_deg: float
    velocity_overshoot_tolerance: float
    stop_behavior: str
    source_path: str

    @classmethod
    def load(cls, name: str = "maira_sim") -> "RobotProfile":
        path = _PROFILE_ROOT / f"{name}.yaml"
        return cls.from_file(path)

    @classmethod
    def from_file(cls, path: pathlib.Path | str) -> "RobotProfile":
        path = pathlib.Path(path)
        with open(path, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f)
        joints = tuple(JointLimits.from_dict(j) for j in raw["joints"])
        ctrl = raw.get("controller", {})
        acc = raw.get("accuracy", {})
        return cls(
            name=raw["name"],
            manufacturer=raw.get("manufacturer", ""),
            model=raw.get("model", raw["name"]),
            dof=int(raw["dof"]),
            cycle_hz=int(ctrl.get("cycle_hz", 1000)),
            joints=joints,
            position_tolerance_deg=float(acc.get("position_tolerance_deg", 0.01)),
            velocity_overshoot_tolerance=float(
                acc.get("velocity_overshoot_tolerance", 0.05)
            ),
            stop_behavior=str(ctrl.get("stop_behavior", "immediate_stop")),
            source_path=str(path),
        )

    @property
    def joint_velocity_limits(self) -> list[float]:
        return [j.max_vel for j in self.joints]
