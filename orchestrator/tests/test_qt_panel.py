"""PyQt5 桌面示教器离线仿真后端契约测试（MockBackend）。

不依赖 GUI / agent：用 QCoreApplication.processEvents() 驱动 QTimer 异步回调，
覆盖与 Web 示教器一致的控制器契约：
  使能门禁（未使能 jog/home/move 拒绝）→ 使能后 jog 增量（步长可切）→
  回零 → 绝对运动 → 复位清报警 → alarm 派生。
"""

from __future__ import annotations

import pathlib
import sys

ORCH = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ORCH))

import pytest
from PyQt5.QtCore import QCoreApplication

from teach_pendant.mock_backend import DOF, JOG_STEP_DEG, MockBackend

_app: QCoreApplication | None = None


def _ensure_app() -> QCoreApplication:
    global _app
    if _app is None:
        _app = QCoreApplication.instance() or QCoreApplication([])
    return _app


def _call(backend, fn, *args, **kw):
    """同步包装异步回调：调用后 processEvents 直到回调执行（QTimer.singleShot(0)）。"""
    out = {}

    def cb(d, e):
        out["d"], out["e"] = d, e

    fn(*args, cb=cb, **kw)
    _ensure_app().processEvents()
    assert "d" in out or "e" in out, "回调未执行（事件循环未驱动）"
    return out.get("d"), out.get("e")


@pytest.fixture()
def backend() -> MockBackend:
    return MockBackend()


def test_servo_gates_jog_home_move(backend):
    """未使能时 jog / home / move_absolute 拒绝；servo ON 后全部放行。"""
    d, e = _call(backend, backend.jog, 0, 1)
    assert d is None and e == "not enabled"
    d, e = _call(backend, backend.home)
    assert d is None and e == "not enabled"
    d, e = _call(backend, backend.move_absolute, [0.0] * DOF)
    assert d is None and e == "not enabled"

    d, e = _call(backend, backend.servo, True)
    assert d and d["ok"] and d["enabled"] is True
    assert backend.motion_state == "ACTIVE"

    d, e = _call(backend, backend.jog, 0, 1)
    assert d and d["ok"]
    d, e = _call(backend, backend.home)
    assert d and d["homed"]


def test_jog_increments_joint_by_step(backend):
    """jog 按当前步长增量（正/负），支持自定义步长（HMI 倍率语义）。"""
    _call(backend, backend.servo, True)
    d, _ = _call(backend, backend.jog, 2, 1)
    assert d["step_deg"] == JOG_STEP_DEG
    assert backend.joints[2] == pytest.approx(JOG_STEP_DEG)
    _call(backend, backend.jog, 2, -1)
    assert backend.joints[2] == pytest.approx(0.0)
    # 自定义步长 ×0.5
    _call(backend, backend.jog, 3, 1, step=0.5)
    assert backend.joints[3] == pytest.approx(0.5)
    # 其他轴不受影响
    assert backend.joints[0] == pytest.approx(0.0)


def test_home_zeroes_all_joints(backend):
    """HOME 回零：全部关节归 0 且状态 HOMED。"""
    _call(backend, backend.servo, True)
    _call(backend, backend.jog, 1, 1)
    _call(backend, backend.jog, 4, -1)
    assert backend.joints[1] == pytest.approx(JOG_STEP_DEG)
    assert backend.joints[4] == pytest.approx(-JOG_STEP_DEG)
    d, _ = _call(backend, backend.home)
    assert d["homed"] and d["motion_state"] == "HOMED"
    assert backend.joints == [0.0] * DOF


def test_move_absolute_sets_target(backend):
    """Move Absolute：目标角整体写入关节。"""
    _call(backend, backend.servo, True)
    target = [10.0, -20.0, 30.0, 0.0, 90.0, 0.0, 0.0]
    assert len(target) == DOF
    d, e = _call(backend, backend.move_absolute, target)
    assert d and d["moved"] and e is None
    assert backend.joints == [float(v) for v in target]


def test_stop_reset_and_alarm_derivation(backend):
    """stop 置停；error 状态 → alarm 派生报警；reset 清报警恢复。"""
    _call(backend, backend.servo, True)
    d, _ = _call(backend, backend.stop)
    assert d["stopped"] is True

    backend.error, backend.error_code, backend.error_message = True, 0x1001, "estop pressed"
    d, e = _call(backend, backend.alarm)
    assert d["error"] is True and d["alarms"][0]["code"] == 0x1001

    _call(backend, backend.reset)
    assert backend.error is False and backend.error_code == 0
    assert backend.motion_state == "ACTIVE"  # 使能仍在
    d, _ = _call(backend, backend.alarm)
    assert d["error"] is False and d["alarms"] == []
