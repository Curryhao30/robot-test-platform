"""Position Oracle：终位误差判定。

判定项：|q_final - q_target| <= tolerance
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class PositionCheck:
    expected: float
    actual: float
    error: float
    limit: float
    passed: bool
    axis: int = 0

    def summary(self) -> str:
        return (
            f"axis[{self.axis}] Expected: {self.expected:.3f} deg | "
            f"Actual: {self.actual:.3f} deg | Error: {self.error:.4f} deg | "
            f"Limit: {self.limit:.4f} deg | {'PASS' if self.passed else 'FAIL'}"
        )


def position_error(actual: float, target: float, tolerance: float,
                   axis: int = 0) -> PositionCheck:
    err = abs(float(actual) - float(target))
    return PositionCheck(
        expected=float(target),
        actual=float(actual),
        error=err,
        limit=float(tolerance),
        passed=err <= float(tolerance),
        axis=axis,
    )


def multi_axis_position(q_final: list[float], q_target: list[float],
                        tolerance: float) -> list[PositionCheck]:
    return [
        position_error(q_final[i], q_target[i], tolerance, axis=i)
        for i in range(len(q_target))
    ]
