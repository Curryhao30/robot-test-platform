"""示教器离线仿真控制器后端（MockBackend）。

独立成模块：QT 桌面面板（qt_panel.py）与 pytest 契约测试共用，
避免测试侧引入 QtWebEngineWidgets（CI / 无显示环境更稳）。

仅依赖 PyQt5.QtCore.QTimer —— 异步回调语义与面板轮询（在途防堆积）一致，
测试侧用 QCoreApplication.processEvents() 驱动回调即可，无需 GUI / agent。
"""

from __future__ import annotations

from PyQt5.QtCore import QTimer

DOF = 7
JOG_STEP_DEG = 5.0  # 服务端默认步长（桌面 HMI 可切倍率）


class MockBackend:
    """内置仿真控制器：无需 agent，离线演示 QT 面板。

    仅实现示教器契约的最小状态机（使能 / 增量 jog / 回零 / 绝对运动 / 复位 / 报警），
    与真实 REST 返回结构一致，便于面板 UI 逻辑通用。
    """

    def __init__(self):
        self.enabled = False
        self.moving = False
        self.error = False
        self.error_code = 0
        self.error_message = ""
        self.motion_state = "IDLE"
        self.joints = [0.0] * DOF

    def _reply(self, payload, cb):
        if cb:
            QTimer.singleShot(0, lambda: cb(payload, None))

    def info(self, cb):
        self._reply({"name": "mock_maira", "dof": DOF, "cycle_hz": 1000}, cb)

    def status(self, cb):
        self._reply({
            "enabled": self.enabled, "moving": self.moving, "error": self.error,
            "motion_state": self.motion_state, "joints": list(self.joints),
            "error_code": self.error_code, "error_message": self.error_message,
        }, cb)

    def alarm(self, cb):
        alarms = [{"code": self.error_code, "message": self.error_message}] if self.error else []
        self._reply({"error": self.error, "alarms": alarms}, cb)

    def servo(self, on: bool, cb):
        self.enabled = on
        self.motion_state = "ACTIVE" if on else "IDLE"
        self._reply({"ok": True, "enabled": on, "motion_state": self.motion_state}, cb)

    def jog(self, joint: int, direction: int, cb, step: float | None = None):
        if not self.enabled:
            if cb:
                cb(None, "not enabled")
            return
        self.joints[joint] += (step or JOG_STEP_DEG) * direction
        self._reply({"ok": True, "axis": joint, "dir": direction,
                     "step_deg": step or JOG_STEP_DEG}, cb)

    def stop(self, cb):
        self.moving = False
        self._reply({"ok": True, "stopped": True}, cb)

    def reset(self, cb):
        self.error = False
        self.error_code = 0
        self.error_message = ""
        self.motion_state = "ACTIVE" if self.enabled else "IDLE"
        self._reply({"ok": True, "reset": True}, cb)

    def home(self, cb):
        if not self.enabled:
            if cb:
                cb(None, "not enabled")
            return
        self.joints = [0.0] * DOF
        self._reply({"ok": True, "homed": True, "motion_state": "HOMED"}, cb)

    def move_absolute(self, target: list[float], cb):
        if not self.enabled:
            if cb:
                cb(None, "not enabled")
            return
        self.joints = [float(t) for t in target]
        self._reply({"ok": True, "moved": True}, cb)
