"""Velocity Oracle：速度约束判定。

判定项：max|dq/dt| <= Vmax_profile * (1 + tolerance)   （控制器对请求速度做限速钳制）
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass
class VelocityCheck:
    peak: float
    limit: float
    passed: bool
    axis: int = 0

    def summary(self) -> str:
        return (
            f"axis[{self.axis}] Peak |v|: {self.peak:.3f} deg/s | "
            f"Limit: {self.limit:.3f} deg/s | {'PASS' if self.passed else 'FAIL'}"
        )


def max_abs_velocity(dq: np.ndarray, axis: int) -> float:
    """dq: (dof, n)"""
    return float(np.max(np.abs(dq[axis])))


def velocity_limit_check(dq: np.ndarray, axis: int, limit: float,
                         tolerance: float = 0.05) -> VelocityCheck:
    peak = max_abs_velocity(dq, axis)
    effective = limit * (1.0 + tolerance)
    return VelocityCheck(peak=peak, limit=effective, passed=peak <= effective,
                         axis=axis)
