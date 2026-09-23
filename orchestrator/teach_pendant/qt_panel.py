"""PyQt5 桌面示教器面板（P2.3 路径 B 的第三种示教器客户端）。

与浏览器 UI 共享同一控制器契约：本面板通过 REST 调用 teach_pendant.main:app
（/api/servo、/api/jog、/api/status、/api/home …），是示教器的**桌面 HMI** 形态，
用于补齐 QT/C++ 桌面开发能力（对应华沿岗位"示教器软件开发 / 脚本语言 QT/HMI"要求）。

工程结构（贴近工业 Qt Creator 工作流）：
    qt_panel.ui        Qt Designer 设计的 UI（控件 + 布局）
    ui_qt_panel.py     `pyuic5 qt_panel.ui -o ui_qt_panel.py` 生成的 UI 类（勿手改）
    qt_panel.py        业务类 QTTeachPendant（加载 UI + 信号连接 + 控制逻辑）
若修改 qt_panel.ui，需重新执行：py -m PyQt5.uic.pyuic teach_pendant/qt_panel.ui -o teach_pendant/ui_qt_panel.py

运行:
    # 真实模式：先起 teach_pendant 后端（连控制器 gRPC）
    python -m uvicorn teach_pendant.main:app --port 58081
    python -m teach_pendant.qt_panel --url http://127.0.0.1:58081
    # 离线演示（无需控制器/agent）：内置 Mock 控制器
    python -m teach_pendant.qt_panel --mock
"""
from __future__ import annotations

import argparse
import json
import sys

from PyQt5.QtCore import QTimer, QUrl
from PyQt5.QtNetwork import QNetworkAccessManager, QNetworkRequest
from PyQt5.QtWidgets import QApplication, QMainWindow

try:
    from teach_pendant.ui_qt_panel import Ui_QtTeachPendant
except ImportError:  # 直接脚本运行回退
    from ui_qt_panel import Ui_QtTeachPendant


JOG_STEP_DEG = 5.0
DOF = 7


# --------------------------------------------------------------------------
# 后端抽象：REST（真实）与 Mock（离线）两种实现，UI 只依赖统一接口
# --------------------------------------------------------------------------
class RestBackend:
    """REST 后端：用 QNetworkAccessManager 调 teach_pendant REST（QT 原生网络栈）。"""

    def __init__(self, base_url: str):
        self.base = base_url.rstrip("/")
        self.nam = QNetworkAccessManager()

    def _call(self, method: str, path: str, body=None, cb=None):
        url = QUrl(self.base + path)
        req = QNetworkRequest(url)
        req.setHeader(QNetworkRequest.ContentTypeHeader, "application/json")
        if method == "GET":
            reply = self.nam.get(req)
        else:
            data = json.dumps(body or {}).encode("utf-8")
            reply = self.nam.post(req, data)
        reply.finished.connect(lambda: self._on_finished(reply, cb))

    def _on_finished(self, reply, cb):
        try:
            raw = bytes(reply.readAll()).decode("utf-8", "replace")
            status = reply.attribute(QNetworkRequest.HttpStatusCodeAttribute)
            if status and 200 <= status < 300:
                data = json.loads(raw) if raw else {}
                if cb:
                    cb(data, None)
            else:
                if cb:
                    cb(None, f"HTTP {status}: {raw[:200]}")
        except Exception as e:  # noqa: BLE001
            if cb:
                cb(None, str(e))
        finally:
            reply.deleteLater()

    def info(self, cb):
        self._call("GET", "/api/info", cb=cb)

    def status(self, cb):
        self._call("GET", "/api/status", cb=cb)

    def alarm(self, cb):
        self._call("GET", "/api/alarm", cb=cb)

    def servo(self, on: bool, cb):
        self._call("POST", "/api/servo", {"on": on}, cb=cb)

    def jog(self, joint: int, direction: int, cb):
        self._call("POST", "/api/jog", {"joint": joint, "direction": direction}, cb=cb)

    def stop(self, cb):
        self._call("POST", "/api/stop", cb=cb)

    def reset(self, cb):
        self._call("POST", "/api/reset", cb=cb)

    def home(self, cb):
        self._call("POST", "/api/home", cb=cb)

    def move_absolute(self, target: list[float], cb):
        self._call("POST", "/api/move_absolute", {"target": target}, cb=cb)


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

    def jog(self, joint: int, direction: int, cb):
        if not self.enabled:
            if cb:
                cb(None, "not enabled")
            return
        self.joints[joint] += JOG_STEP_DEG * direction
        self._reply({"ok": True, "axis": joint, "dir": direction}, cb)

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


# --------------------------------------------------------------------------
# 业务类：加载 UI + 信号连接 + 控制逻辑
# --------------------------------------------------------------------------
class QTTeachPendant(QMainWindow, Ui_QtTeachPendant):
    def __init__(self, backend=None, mock: bool = False,
                 url: str = "http://127.0.0.1:58081"):
        super().__init__()
        self.setupUi(self)
        self.backend = backend or (MockBackend() if mock else RestBackend(url))

        # 收集动态控件
        self.joint_labels = [getattr(self, f"jval_{i}") for i in range(DOF)]
        self.jog_buttons: list = []
        self.target_inputs = [getattr(self, f"ainp_{i}") for i in range(DOF)]
        for i in range(DOF):
            self.jog_buttons += [getattr(self, f"jminus_{i}"), getattr(self, f"jplus_{i}")]
            getattr(self, f"jminus_{i}").clicked.connect(
                lambda _=False, j=i: self.do_jog(j, -1))
            getattr(self, f"jplus_{i}").clicked.connect(
                lambda _=False, j=i: self.do_jog(j, 1))

        # 指令按钮
        self.btn_servo_on.clicked.connect(lambda: self.do_servo(True))
        self.btn_servo_off.clicked.connect(lambda: self.do_servo(False))
        self.btn_stop.clicked.connect(self.do_stop)
        self.btn_reset.clicked.connect(self.do_reset)
        self.btn_home.clicked.connect(self.do_home)
        self.btn_move.clicked.connect(self.do_move_absolute)

        self._set_led(False)
        self._poll = QTimer(self)
        self._poll.setInterval(500)
        self._poll.timeout.connect(self.refresh_status)
        self._poll.start()
        self.refresh_status()

    # -- 外观辅助 -----------------------------------------------------------
    def _set_led(self, on: bool, color: str = "#22c55e"):
        c = color if on else "#64748b"
        self.led.setStyleSheet(
            f"background:{c};border-radius:7px;border:1px solid #334155")

    def _log(self, msg: str):
        self.log.append(msg)

    # -- 操作 ---------------------------------------------------------------
    def do_servo(self, on: bool):
        self.backend.servo(on, lambda d, e: self._log(
            f"servo {'ON' if on else 'OFF'}: {self._fmt(d, e)}"))

    def do_jog(self, joint: int, direction: int):
        self.backend.jog(joint, direction, lambda d, e: self._log(
            f"jog J{joint} {'+' if direction > 0 else '-'}: {self._fmt(d, e)}"))

    def do_stop(self):
        self.backend.stop(lambda d, e: self._log(f"stop: {self._fmt(d, e)}"))

    def do_reset(self):
        self.backend.reset(lambda d, e: self._log(f"reset: {self._fmt(d, e)}"))

    def do_home(self):
        self.backend.home(lambda d, e: self._log(f"home: {self._fmt(d, e)}"))

    def do_move_absolute(self):
        try:
            target = [float(inp.text()) for inp in self.target_inputs]
        except ValueError:
            self._log("Move: 目标角度需为数字")
            return
        self.backend.move_absolute(target, lambda d, e: self._log(
            f"move_abs: {self._fmt(d, e)}"))

    @staticmethod
    def _fmt(data, err):
        if err:
            return f"ERR {err}"
        return json.dumps(data, ensure_ascii=False)

    # -- 轮询刷新 -----------------------------------------------------------
    def refresh_status(self):
        self.backend.status(self._on_status)
        self.backend.alarm(self._on_alarm)

    def _on_status(self, d, e):
        if e:
            self._set_led(False, "#ef4444")
            self.title.setText("示教器（连接异常）")
            return
        self.title.setText(f"示教器 · {d.get('motion_state', '?')} · "
                           f"{'ENABLED' if d.get('enabled') else 'disabled'}")
        self._set_led(bool(d.get("enabled")),
                     "#ef4444" if d.get("error") else "#22c55e")
        joints = d.get("joints") or []
        for i, lab in enumerate(self.joint_labels):
            lab.setText(f"{joints[i]:.1f}" if i < len(joints) else "?")
        can_jog = bool(d.get("enabled")) and not d.get("error")
        for b in self.jog_buttons:
            b.setEnabled(can_jog)
        self.btn_move.setEnabled(can_jog)

    def _on_alarm(self, d, e):
        if e or not d or not d.get("error"):
            self.alarm_label.setText("—")
            return
        msgs = "；".join(a.get("message", "") for a in d.get("alarms", []))
        self.alarm_label.setText(f"[{d.get('alarms', [{}])[0].get('code', '?')}] {msgs}")


def main() -> None:
    ap = argparse.ArgumentParser(description="PyQt5 桌面示教器面板")
    ap.add_argument("--url", default="http://127.0.0.1:58081",
                    help="teach_pendant REST 基址（真实模式）")
    ap.add_argument("--mock", action="store_true",
                    help="离线模式：内置仿真控制器，无需 agent")
    args = ap.parse_args()

    app = QApplication(sys.argv)
    win = QTTeachPendant(mock=args.mock, url=args.url)
    win.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
