"""Timing Oracle：控制周期采样分析（Python 侧只做统计，不做实时执行）。

判定项：采样间隔均值 ≈ 1/cycle_hz；jitter = max|dt - expected|；
        jitter 相对容差（默认 50%，P0 宽松阈值）。
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass
class TimingCheck:
    cycle_hz: int
    expected_us: float
    mean_us: float
    jitter_us: float
    passed: bool
    samples: int

    def summary(self) -> str:
        return (
            f"Cycle={self.cycle_hz}Hz expected={self.expected_us:.1f}us | "
            f"mean={self.mean_us:.1f}us | max jitter={self.jitter_us:.1f}us | "
            f"samples={self.samples} | {'PASS' if self.passed else 'FAIL'}"
        )


def sampling_stats(t_ns: np.ndarray) -> dict:
    if t_ns.size < 2:
        return {"mean_us": 0.0, "jitter_us": 0.0, "min_us": 0.0, "max_us": 0.0}
    dt = np.diff(t_ns) / 1000.0  # ns -> us
    return {
        "mean_us": float(np.mean(dt)),
        "jitter_us": float(np.max(np.abs(dt - np.mean(dt)))),
        "min_us": float(np.min(dt)),
        "max_us": float(np.max(dt)),
    }


def cycle_check(t_ns: np.ndarray, cycle_hz: int,
                jitter_tolerance: float = 0.5) -> TimingCheck:
    stats = sampling_stats(t_ns)
    expected_us = 1_000_000.0 / cycle_hz
    passed = stats["jitter_us"] <= expected_us * jitter_tolerance
    return TimingCheck(
        cycle_hz=cycle_hz,
        expected_us=expected_us,
        mean_us=stats["mean_us"],
        jitter_us=stats["jitter_us"],
        passed=passed,
        samples=int(t_ns.size),
    )
