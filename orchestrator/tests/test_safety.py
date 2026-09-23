"""P2.2a 用例：安全联锁仿真（SafetyStateMachine + 控制器联动）。

覆盖（纯软件，CI 可跑）：
  - 初始 SAFE：允许使能；
  - ESTOP：最高优先级——禁止使能 + 停机 + 抱闸，且联动控制器急停（FAULT，
    后续运动被拒 ERR_EMERGENCY_STOP）；
  - 优先级：ESTOP > 门开 > 驱动器故障；
  - 门开：Guard Stop（正常停止语义）+ 禁止使能；
  - 驱动器故障：FAULT 状态 + 停机请求；
  - 恢复：清除安全输入后回 SAFE、重新允许使能。

边界声明（同 p2-design.md §5.3）：仿真层只验证安全逻辑与联锁语义，
真实安全 I/O 与安全等级由 P2.2b HIL 阶段验证。
"""
from __future__ import annotations

import pytest

from app.client import ERR_EMERGENCY_STOP, ERR_NOT_ENABLED
from tests import capture
from tests.test_p0_core import DEG_7


@pytest.fixture(autouse=True)
def _clean_safety(client):
    """每个用例前后清安全输入；联动急停可能留下 FAULT，用 reset 恢复控制器，
    避免污染同进程共享 agent（否则后续示教器等用例 enable 失败）。"""
    client.set_safety_input()
    client.reset()
    yield
    client.set_safety_input()
    client.reset()


def test_safety_initial_safe(client):
    r = client.get_safety_state()
    assert r.outputs.state == "SAFE", r.outputs
    assert r.outputs.allow_enable is True
    assert r.outputs.stop_required is False
    assert r.outputs.brake_request is False
    capture.DETAILS["test_safety_initial_safe"] = "初始 SAFE：允许使能"


def test_estop_interlock_and_controller_link(client):
    """ESTOP 注入 -> 联锁输出 + 联动控制器急停（FAULT -> 运动被拒）。"""
    client.enable()
    r0 = client.move_absolute([0.0] * 7, velocity=60.0)
    assert r0.ok and r0.motion_state == "DONE", r0.error_message
    r = client.set_safety_input(estop=True)
    assert r.ok and r.outputs.state == "ESTOP", r.outputs
    assert r.outputs.allow_enable is False
    assert r.outputs.stop_required is True
    assert r.outputs.brake_request is True
    assert r.outputs.error_code == 1, r.outputs
    # 联动：控制器急停（FAULT + 掉使能），后续运动被拒
    r2 = client.move_absolute([30.0] + DEG_7[1:], velocity=60.0)
    assert not r2.ok, r2
    assert r2.error_code in (ERR_EMERGENCY_STOP, ERR_NOT_ENABLED), r2
    st = client.get_state()
    assert st.error or not st.enabled, st
    capture.DETAILS["test_estop_interlock_and_controller_link"] = (
        "ESTOP -> 禁止使能+停机+抱闸；联动控制器急停（FAULT/掉使能，move 被拒）")


def test_estop_priority_over_door_and_fault(client):
    """优先级：ESTOP 同时存在门开/驱动器故障时仍为 ESTOP。"""
    r = client.set_safety_input(estop=True, door_open=True, drive_fault=True)
    assert r.outputs.state == "ESTOP" and r.outputs.error_code == 1, r.outputs
    capture.DETAILS["test_estop_priority_over_door_and_fault"] = (
        "ESTOP > 门开 > 驱动器故障（同时注入仍判 ESTOP）")


def test_door_open_guards_and_blocks_enable(client):
    """门开 -> GUARDED：禁止使能（Guard Stop 语义，非 FAULT）。"""
    r = client.set_safety_input(door_open=True)
    assert r.outputs.state == "GUARDED" and r.outputs.error_code == 2, r.outputs
    assert r.outputs.allow_enable is False and r.outputs.stop_required is True
    capture.DETAILS["test_door_open_guards_and_blocks_enable"] = (
        "门开 -> GUARDED：禁止使能 + 停机请求")


def test_drive_fault_state(client):
    r = client.set_safety_input(drive_fault=True)
    assert r.outputs.state == "FAULT" and r.outputs.error_code == 3, r.outputs
    assert r.outputs.allow_enable is False and r.outputs.stop_required is True
    capture.DETAILS["test_drive_fault_state"] = "驱动器故障 -> FAULT：禁止使能 + 停机"


def test_brake_not_released_blocks_enable(client):
    """抱闸未释放（其余正常）-> 禁止使能防坠落，但不要求停机。"""
    r = client.set_safety_input(brake_released=False)
    assert r.outputs.state == "SAFE", r.outputs
    assert r.outputs.allow_enable is False
    assert r.outputs.stop_required is False
    capture.DETAILS["test_brake_not_released_blocks_enable"] = (
        "抱闸未释放 -> 禁止使能（防坠落），不要求停机")


def test_safety_recovery_after_clear(client):
    """清除安全输入 -> 回 SAFE，重新允许使能。"""
    client.set_safety_input(estop=True)
    r = client.set_safety_input()  # 全清
    assert r.outputs.state == "SAFE" and r.outputs.error_code == 0, r.outputs
    assert r.outputs.allow_enable is True
    st = client.get_safety_state()
    assert st.inputs.estop is False and st.outputs.state == "SAFE"
    capture.DETAILS["test_safety_recovery_after_clear"] = (
        "清除安全输入后回 SAFE，重新允许使能")
