"""State Machine Oracle：PLCopen 运动状态序列合法性判定。

MC_MoveAbsolute 语义：Execute -> Busy -> Active -> Done
异常路径：Stop/新命令/急停 -> CommandAborted；故障 -> Error(ErrorID)
"""
from __future__ import annotations

from dataclasses import dataclass

MOTION_ORDER = {"IDLE": 0, "BUSY": 1, "ACTIVE": 2, "DONE": 3,
                "ABORTED": 3, "ERROR": 3}


@dataclass
class StateMachineCheck:
    name: str
    passed: bool
    detail: str
    states_seen: list[str]

    def summary(self) -> str:
        return (
            f"{self.name}: {'PASS' if self.passed else 'FAIL'} | "
            f"states={self.states_seen} | {self.detail}"
        )


def check_move_sequence(states: list[str], expected_final: str = "DONE",
                        name: str = "MoveSequence") -> StateMachineCheck:
    """校验状态序列合法且以预期状态结束。

    - 不允许 ERROR 出现在成功路径
    - 最终状态必须等于 expected_final
    - BUSY/ACTIVE/DONE 顺序出现（允许采样跳过中间态）
    """
    seen = list(dict.fromkeys(states))  # 去重保序
    if not seen:
        return StateMachineCheck(name, False, "empty states", seen)
    final = seen[-1]
    if final != expected_final:
        return StateMachineCheck(
            name, False,
            f"final={final} != expected={expected_final}", seen)
    if "ERROR" in seen and expected_final != "ERROR":
        return StateMachineCheck(name, False, "unexpected ERROR in path", seen)
    # 顺序约束：BUSY -> ACTIVE -> DONE
    idx = {s: i for i, s in enumerate(seen)}
    if "DONE" in idx and "ACTIVE" in idx and idx["ACTIVE"] > idx["DONE"]:
        return StateMachineCheck(name, False, "ACTIVE after DONE", seen)
    return StateMachineCheck(name, True, f"final={final}", seen)


def check_aborted_sequence(states: list[str],
                           name: str = "AbortSequence") -> StateMachineCheck:
    """停止/中止路径：以 ABORTED 结束，且之前出现过 BUSY/ACTIVE。"""
    seen = list(dict.fromkeys(states))
    if not seen:
        return StateMachineCheck(name, False, "empty states", seen)
    if seen[-1] != "ABORTED":
        return StateMachineCheck(
            name, False, f"final={seen[-1]} != ABORTED", seen)
    if not any(s in ("BUSY", "ACTIVE") for s in seen):
        return StateMachineCheck(name, False, "no BUSY/ACTIVE before abort", seen)
    return StateMachineCheck(name, True, "aborted after motion", seen)


def check_error_sequence(states: list[str],
                         name: str = "ErrorSequence") -> StateMachineCheck:
    """错误路径：以 ERROR 结束。"""
    seen = list(dict.fromkeys(states))
    if not seen:
        return StateMachineCheck(name, False, "empty states", seen)
    if seen[-1] != "ERROR":
        return StateMachineCheck(name, False,
                                 f"final={seen[-1]} != ERROR", seen)
    return StateMachineCheck(name, True, "error state reached", seen)
