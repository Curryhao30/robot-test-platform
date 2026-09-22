"""Trajectory Oracle：轨迹形态判定（P0 子集）。

P0：无异常跳变（位置突变）、终点收敛、无反向震荡（最终单调逼近）。
MoveLinear / MoveCircular 的 TCP 路径偏差在加入 FK 后扩展。
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass
class TrajectoryCheck:
    name: str
    passed: bool
    detail: str

    def summary(self) -> str:
        return f"{self.name}: {'PASS' if self.passed else 'FAIL'} | {self.detail}"


def no_jump_check(q: np.ndarray, threshold_deg: float = 0.5) -> TrajectoryCheck:
    """连续采样点之间不允许出现位置突变（>threshold）。"""
    dof, n = q.shape
    worst = 0.0
    for a in range(dof):
        step = np.abs(np.diff(q[a]))
        if step.size:
            worst = max(worst, float(np.max(step)))
    passed = worst <= threshold_deg
    return TrajectoryCheck(
        "No-Jump", passed,
        f"max step = {worst:.4f} deg, threshold = {threshold_deg:.2f} deg",
    )


def monotonic_final_check(q: np.ndarray, axis: int) -> TrajectoryCheck:
    """到达终点前位置单调逼近（不反向震荡）；若移动量极小则视为通过。"""
    seg = q[axis]
    if seg.size < 3:
        return TrajectoryCheck("Monotonic-Final", True, "insufficient samples")
    total = abs(seg[-1] - seg[0])
    if total < 1e-6:
        return TrajectoryCheck("Monotonic-Final", True, "no motion")
    # 归一化后检查回退比例
    back = 0.0
    for i in range(1, seg.size):
        if abs(seg[i] - seg[i - 1]) > 1e-9:
            d = seg[i] - seg[i - 1]
            back = max(back, float(abs(d) / total))
    # 允许 1% 以内的数值回退（离散采样噪声）
    passed = back <= 0.01
    return TrajectoryCheck(
        "Monotonic-Final", passed,
        f"max normalized step-back = {back:.4f}",
    )
