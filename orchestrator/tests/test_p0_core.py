"""P0 核心用例：PLCopen 运动状态测试（pytest -> gRPC -> C++ Agent -> Simulator）。

覆盖：正常执行 / 位置精度 / 速度限速 / 限位保护 / 运动中停止 / 未使能 /
      急停与复位 / 运动中新命令（中止）。
"""
from __future__ import annotations

import threading
import time

import pytest

from app.client import (ERR_ABORTED, ERR_EMERGENCY_STOP, ERR_JOINT_LIMIT,
                        ERR_NOT_ENABLED, Trajectory)
from oracle import position as pos_oracle
from oracle import state_machine as sm_oracle
from oracle import timing as timing_oracle
from oracle import trajectory as traj_oracle
from oracle import velocity as vel_oracle
from tests import capture

DEG_7 = [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]


def _move_target(target, velocity=60.0, acc=150.0, dec=150.0, abort=False, client=None):
    return client.move_absolute(target, velocity=velocity, acceleration=acc,
                                deceleration=dec, abort_current=abort)


def _async(fn):
    """在后台线程执行阻塞 RPC，返回 (thread, result_holder)。"""
    holder: dict = {}

    def run():
        holder["result"] = fn()

    t = threading.Thread(target=run)
    t.start()
    return t, holder


# ---------------------------------------------------------------------------
# 1. Servo Enable
# ---------------------------------------------------------------------------
def test_servo_enable(client):
    r = client.enable()
    assert r.ok, r.error_message
    s = client.get_state()
    assert s.enabled
    assert s.motion_state in ("IDLE", "DONE")
    capture.DETAILS["test_servo_enable"] = (
        f"enabled={s.enabled}, motion_state={s.motion_state}"
    )


# ---------------------------------------------------------------------------
# 2. Axis Home（回零运动）
# ---------------------------------------------------------------------------
def test_axis_home(client, profile):
    client.enable()
    r = client.home(velocity=60.0, acceleration=150.0, deceleration=150.0)
    assert r.ok, r.error_message
    assert r.motion_state == "DONE", r.motion_state
    s = client.get_state()
    assert all(abs(j.position) < profile.position_tolerance_deg for j in s.joints)
    capture.DETAILS["test_axis_home"] = (
        f"final positions = {[round(j.position, 4) for j in s.joints]}"
    )


# ---------------------------------------------------------------------------
# 3. MC_MoveAbsolute 基本执行
# ---------------------------------------------------------------------------
def test_move_absolute_basic(client):
    client.enable()
    target = [0.0, -30.0, 60.0, 0.0, 90.0, 0.0, 0.0]
    r = client.move_absolute(target, velocity=60.0)
    assert r.ok, r.error_message
    assert r.motion_state == "DONE", r.motion_state
    s = client.get_state()
    assert not s.moving
    capture.DETAILS["test_move_absolute_basic"] = (
        f"motion_state={r.motion_state}, samples={r.sample_count}"
    )


# ---------------------------------------------------------------------------
# 4. 位置精度（Position Oracle）
# ---------------------------------------------------------------------------
def test_position_accuracy(client, profile):
    client.enable()
    target = [15.0, -45.0, 90.0, -15.0, 60.0, 10.0, -5.0]
    r = client.move_absolute(target, velocity=60.0)
    assert r.ok, r.error_message
    tr = Trajectory.from_result(r)
    assert tr.q.shape[1] > 0
    final = tr.q[:, -1]
    checks = pos_oracle.multi_axis_position(
        final.tolist(), target, profile.position_tolerance_deg)
    assert all(c.passed for c in checks), "\n".join(c.summary() for c in checks)
    capture.DETAILS["test_position_accuracy"] = "\n".join(
        c.summary() for c in checks)
    capture.TRAJECTORY = (tr.t_ns, tr.q, tr.dq, tr.ddq)


# ---------------------------------------------------------------------------
# 5. 速度限速（Velocity Oracle：请求速度 > 轴限速 -> 控制器钳制）
# ---------------------------------------------------------------------------
def test_velocity_limit(client, profile):
    client.enable()
    axis_limit = profile.joints[0].max_vel  # 120 deg/s
    target = [90.0] + DEG_7[1:]
    r = client.move_absolute(target, velocity=400.0, acc=300.0, dec=300.0)
    assert r.ok, r.error_message
    tr = Trajectory.from_result(r)
    check = vel_oracle.velocity_limit_check(
        tr.dq, axis=0, limit=axis_limit,
        tolerance=profile.velocity_overshoot_tolerance)
    assert check.passed, check.summary()
    assert check.peak > 60.0, "轴 0 实际峰值速度异常偏低（未运动？）"
    capture.DETAILS["test_velocity_limit"] = check.summary()


# ---------------------------------------------------------------------------
# 6. 限位保护（Joint Limit Protection：目标越界 -> Error，不得运动）
# ---------------------------------------------------------------------------
def test_joint_limit_protection(client, profile):
    client.enable()
    before = client.get_state()
    q0 = before.joints[0].position
    target = [200.0] + DEG_7[1:]  # 轴 0 限位 [-175, 175]
    r = client.move_absolute(target, velocity=60.0)
    assert not r.ok
    assert r.error_code == ERR_JOINT_LIMIT
    assert r.motion_state == "ERROR"
    tr = Trajectory.from_result(r)
    assert tr.q.shape[1] == 0, "限位拒绝时不应产生任何轨迹采样（不得运动）"
    after = client.get_state()
    assert abs(after.joints[0].position - q0) < 1e-9, "轴 0 位置不应改变"
    capture.DETAILS["test_joint_limit_protection"] = (
        f"error_code={r.error_code}, motion_state={r.motion_state}, "
        f"q0={q0:.3f} -> q={after.joints[0].position:.3f} (未移动)"
    )


# ---------------------------------------------------------------------------
# 7. 运动中 Stop（CommandAborted）
# ---------------------------------------------------------------------------
def test_stop_during_motion(client):
    client.enable()
    # 先归位到 -60°，确保后续 150° 长运动不会因“已在目标位”而瞬时完成
    r0 = client.move_absolute([-60.0] + DEG_7[1:], velocity=60.0)
    assert r0.ok and r0.motion_state == "DONE", r0.error_message
    target = [90.0] + DEG_7[1:]
    t, holder = _async(lambda: client.move_absolute(
        target, velocity=30.0, acc=150.0, dec=150.0))
    time.sleep(0.3)  # 运动中（150° @30°/s 全程约 5.2s）
    mid = client.get_state()
    assert mid.moving, f"预期运动中，实际 motion_state={mid.motion_state}"
    client.stop(emergency=False)
    t.join(timeout=10)
    r = holder["result"]
    assert not r.ok
    assert r.error_code == ERR_ABORTED
    assert r.motion_state == "ABORTED"
    tr = Trajectory.from_result(r)
    assert tr.q.shape[1] > 0
    assert tr.q[0, -1] < target[0] - 1.0, "停止时不应已到达目标"
    check = sm_oracle.check_aborted_sequence(tr.motion_state)
    assert check.passed, check.summary()
    s = client.get_state()
    assert not s.moving
    capture.DETAILS["test_stop_during_motion"] = (
        f"aborted at q0={tr.q[0, -1]:.2f} deg (target={target[0]}), "
        f"states={sorted(set(tr.motion_state))}"
    )


# ---------------------------------------------------------------------------
# 8. 未使能运动（NOT_ENABLED）
# ---------------------------------------------------------------------------
def test_move_without_enable(client):
    client.disable()
    r = client.move_absolute([10.0] + DEG_7[1:], velocity=60.0)
    assert not r.ok
    assert r.error_code == ERR_NOT_ENABLED
    s = client.get_state()
    assert not s.moving
    capture.DETAILS["test_move_without_enable"] = (
        f"error_code={r.error_code}, motion_state={r.motion_state}"
    )


# ---------------------------------------------------------------------------
# 9. 急停与复位（Emergency Stop -> FAULT -> Reset）
# ---------------------------------------------------------------------------
def test_emergency_stop_and_reset(client):
    client.enable()
    r0 = client.move_absolute([-60.0] + DEG_7[1:], velocity=60.0)
    assert r0.ok and r0.motion_state == "DONE", r0.error_message
    target = [90.0] + DEG_7[1:]
    t, holder = _async(lambda: client.move_absolute(
        target, velocity=30.0, acc=150.0, dec=150.0))
    time.sleep(0.3)
    client.stop(emergency=True)
    t.join(timeout=10)
    r = holder["result"]
    assert not r.ok
    assert r.error_code == ERR_EMERGENCY_STOP
    s = client.get_state()
    assert s.error and not s.enabled, "急停后应处于 FAULT（error、未使能）"
    # FAULT 下拒绝运动
    r2 = client.move_absolute([10.0] + DEG_7[1:], velocity=60.0)
    assert not r2.ok
    assert r2.error_code == ERR_NOT_ENABLED
    # Reset 恢复
    r3 = client.reset()
    assert r3.ok
    s2 = client.get_state()
    assert s2.enabled and not s2.error
    r4 = client.move_absolute([10.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
                              velocity=60.0)
    assert r4.ok and r4.motion_state == "DONE"
    capture.DETAILS["test_emergency_stop_and_reset"] = (
        f"fault error_code={r.error_code} -> reset -> enabled={s2.enabled}, "
        f"recover move {r4.motion_state}"
    )


# ---------------------------------------------------------------------------
# 10. 运动中下发新命令（abort_current=true -> 旧命令 CommandAborted）
# ---------------------------------------------------------------------------
def test_new_command_aborts_previous(client):
    client.enable()
    r0 = client.move_absolute([-60.0] + DEG_7[1:], velocity=60.0)
    assert r0.ok and r0.motion_state == "DONE", r0.error_message
    t1, h1 = _async(lambda: client.move_absolute(
        [90.0] + DEG_7[1:], velocity=30.0, acc=150.0, dec=150.0))
    time.sleep(0.3)
    t2, h2 = _async(lambda: client.move_absolute(
        [-45.0] + DEG_7[1:], velocity=60.0, acc=150.0, dec=150.0,
        abort_current=True))
    t2.join(timeout=10)
    r2 = h2["result"]
    assert r2.ok and r2.motion_state == "DONE", r2.error_message
    t1.join(timeout=10)
    r1 = h1["result"]
    assert not r1.ok and r1.error_code == ERR_ABORTED
    assert r1.motion_state == "ABORTED"
    tr1 = Trajectory.from_result(r1)
    assert tr1.q[0, -1] < 90.0 - 1.0, "被中止的旧命令不应到达原目标"
    s = client.get_state()
    assert abs(s.joints[0].position - (-45.0)) < 0.01
    capture.DETAILS["test_new_command_aborts_previous"] = (
        f"old: {r1.motion_state} at q0={tr1.q[0, -1]:.2f}; "
        f"new: {r2.motion_state} at q0={s.joints[0].position:.2f}"
    )
