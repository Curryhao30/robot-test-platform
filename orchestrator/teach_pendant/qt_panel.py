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
    py -m uvicorn teach_pendant.main:app --port 58081
    py -m teach_pendant.qt_panel --url http://127.0.0.1:58081
    # 离线演示（无需控制器/agent）：内置 Mock 控制器（--fullscreen 全屏，F11 切换）
    py -m teach_pendant.qt_panel --mock
    py -m teach_pendant.qt_panel --mock --fullscreen
    # "测试与曲线" 标签页：另起管理控制台（提供 /api/summary 等；只读查看无需 agent）
    py -m uvicorn app.console:app --port 58090
    # 完整真实模式（agent + 示教器后端 + 控制台）可用 run_real_backend.py 一键拉起
    py teach_pendant/run_real_backend.py

    # ⚠️ 本项目路径含中文（测试开发），Qt 会因路径非 ASCII 初始化失败。
    # .venv 已junction 到纯 ASCII 的 C:\venvs\rtp，请用 ASCII 真实路径（或先 subst）启动桌面：
    C:\venvs\rtp\Scripts\python.exe -m teach_pendant.qt_panel --mock
    # 推荐设别名：Set-Alias rtp "C:\venvs\rtp\Scripts\python.exe" 后 `rtp -m teach_pendant.qt_panel --mock`
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime


def _ensure_qt_plugins_path() -> None:
    """Windows 上把 Qt plugin 目录转成 8.3 短路径，避免中文字符导致 Qt 找不到 platform plugin。"""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        import os
        from ctypes import wintypes
        import PyQt5
        plugin_dir = os.path.join(os.path.dirname(PyQt5.__file__), "Qt5", "plugins")
        if any(ord(c) > 127 for c in plugin_dir):
            buf = ctypes.create_unicode_buffer(wintypes.MAX_PATH)
            res = ctypes.windll.kernel32.GetShortPathNameW(plugin_dir, buf, wintypes.MAX_PATH)
            if res and res < wintypes.MAX_PATH:
                plugin_dir = buf.value
        os.environ.setdefault("QT_QPA_PLATFORM_PLUGIN_PATH", plugin_dir)
    except Exception:  # noqa: BLE001
        pass


_ensure_qt_plugins_path()


from PyQt5.QtCore import QSettings, Qt, QTimer, QUrl
from PyQt5.QtGui import QFont, QGuiApplication, QKeySequence
from PyQt5.QtNetwork import QNetworkAccessManager, QNetworkRequest
from PyQt5.QtWebEngineWidgets import QWebEngineView
from PyQt5.QtWidgets import QApplication, QMainWindow, QShortcut, QVBoxLayout

try:
    from teach_pendant.ui_qt_panel import Ui_QtTeachPendant
except ImportError:  # 直接脚本运行回退
    from ui_qt_panel import Ui_QtTeachPendant

try:
    from teach_pendant.mock_backend import MockBackend
except ImportError:  # 直接脚本运行回退
    from mock_backend import MockBackend


JOG_STEP_DEG = 5.0                  # 服务端默认步长（桌面 HMI 可切倍率）
JOG_STEPS = [0.1, 1.0, 5.0, 10.0]   # 倍率档位：工业示教器 ×0.1/×1/×5/×10 语义
JOG_REPEAT_FIRST_MS = 350           # 按住 jog 后进入连发的首次延迟
JOG_REPEAT_MS = 120                 # 按住 jog 的连发间隔
POLL_MS = 500                       # 状态轮询周期
LOG_MAX_BLOCKS = 500                # 日志环形上限，防长跑越用越卡
DOF = 7

# 工业 HMI 深色主题：高对比 + 大按钮（车间戴手套可点），关键指令按语义配色
HMI_QSS = """
QWidget { background:#0b1220; color:#dbe6f5;
          font-family:"Segoe UI","Microsoft YaHei"; font-size:11pt; }
QMainWindow, QTabWidget::pane { background:#0b1220; }
QTabBar::tab { padding:8px 18px; border:1px solid #243049; border-radius:4px; }
QTabBar::tab:selected { background:#0ea5e9; color:#04121f; font-weight:600; }
QGroupBox { border:1px solid #243049; border-radius:6px; margin-top:16px;
            font-weight:600; }
QGroupBox::title { subcontrol-origin:margin; left:10px; padding:0 6px; color:#7dd3fc; }
QPushButton { background:#1e293b; border:1px solid #334155; border-radius:6px;
              padding:6px 12px; min-height:30px; }
QPushButton:hover { background:#27364b; border-color:#38bdf8; }
QPushButton:pressed { background:#0ea5e9; color:#04121f; }
QPushButton:disabled { background:#111a2b; color:#4b5a70; border-color:#1b2536; }
QPushButton#btn_stop { background:#7f1d1d; border-color:#b91c1c; color:#fee2e2; }
QPushButton#btn_stop:hover { background:#b91c1c; }
QPushButton#btn_servo_on { background:#14532d; border-color:#22c55e; color:#dcfce7; }
QPushButton#btn_servo_off { background:#4c1d1d; border-color:#f87171; color:#fee2e2; }
QPushButton#btn_home { background:#1e3a5f; border-color:#38bdf8; color:#e0f2fe; }
QLineEdit, QComboBox { background:#0f172a; border:1px solid #334155; border-radius:6px;
                       padding:5px 8px; min-height:26px;
                       selection-background-color:#0ea5e9; }
QTextEdit { background:#060b16; border:1px solid #1e293b; border-radius:6px;
            font-family:Consolas,"Courier New"; font-size:10pt; }
QLabel#title { font-size:14pt; font-weight:600; color:#e2e8f0; }
QStatusBar { background:#0f172a; color:#94a3b8; }
"""


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

    def jog(self, joint: int, direction: int, cb, step: float | None = None):
        body = {"joint": joint, "direction": direction}
        if step:  # 桌面 HMI 倍率；服务端缺省仍用 JOG_STEP_DEG
            body["step_deg"] = float(step)
        self._call("POST", "/api/jog", body, cb=cb)

    def stop(self, cb):
        self._call("POST", "/api/stop", cb=cb)

    def reset(self, cb):
        self._call("POST", "/api/reset", cb=cb)

    def home(self, cb):
        self._call("POST", "/api/home", cb=cb)

    def move_absolute(self, target: list[float], cb):
        self._call("POST", "/api/move_absolute", {"target": target}, cb=cb)


# --------------------------------------------------------------------------
# 业务类：加载 UI + 信号连接 + 控制逻辑
# --------------------------------------------------------------------------
class QTTeachPendant(QMainWindow, Ui_QtTeachPendant):
    def __init__(self, backend=None, mock: bool = False,
                 url: str = "http://127.0.0.1:58081",
                 console_url: str = "http://127.0.0.1:58090"):
        super().__init__()
        self.setupUi(self)
        self.setStyleSheet(HMI_QSS)
        self.backend = backend or (MockBackend() if mock else RestBackend(url))
        self.url = url
        self.console_url = console_url
        self._inflight = 0          # 在途请求数：上一轮没回来就跳过本轮，防堆积
        self._err_streak = 0
        self._last_joints: list[float] = []
        self._t0 = 0.0
        self._jog_joint: int | None = None
        self._jog_dir = 1

        # 标签页文字（pyuic 生成的 addTab 未带文字）
        self.tabWidget.setTabText(0, "示教器")
        self.tabWidget.setTabText(1, "测试与曲线")

        # Tab2：内嵌控制台网页（用例清单 / 运行历史 / 缺陷视图 / 数据曲线），
        # 复用 frontend/index.html，零重写；仅查看不需要 controller agent。
        self.web = QWebEngineView()
        _wl = QVBoxLayout(self.webHost)
        _wl.setContentsMargins(0, 0, 0, 0)
        _wl.addWidget(self.web)
        self.web.setUrl(QUrl(console_url))

        # 收集动态控件：关节值用等宽大字右对齐，jog 键放大到可戴手套点
        self.joint_labels = [getattr(self, f"jval_{i}") for i in range(DOF)]
        self.jog_buttons: list = []
        self.target_inputs = [getattr(self, f"ainp_{i}") for i in range(DOF)]
        _mono = QFont("Consolas", 12)
        _mono.setBold(True)
        for i in range(DOF):
            lab = self.joint_labels[i]
            lab.setMinimumWidth(92)
            lab.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            lab.setFont(_mono)
            minus = getattr(self, f"jminus_{i}")
            plus = getattr(self, f"jplus_{i}")
            for _b, _d in ((minus, -1), (plus, 1)):
                _b.setMinimumSize(48, 38)
                self._bind_jog(_b, i, _d)
            self.jog_buttons += [minus, plus]

        # 指令按钮
        self.btn_servo_on.clicked.connect(lambda: self.do_servo(True))
        self.btn_servo_off.clicked.connect(lambda: self.do_servo(False))
        self.btn_stop.clicked.connect(self.do_stop)
        self.btn_reset.clicked.connect(self.do_reset)
        self.btn_home.clicked.connect(self.do_home)
        self.btn_move.clicked.connect(self.do_move_absolute)
        self.btn_read_cur.clicked.connect(self.do_read_current)
        self.cmb_step.setCurrentIndex(JOG_STEPS.index(JOG_STEP_DEG))

        # 按住连发 jog：pressed 起手一步 → 350ms 后按 120ms 连发 → released 停
        self._jog_repeat = QTimer(self)
        self._jog_repeat.timeout.connect(self._on_jog_repeat)

        # 日志环形缓冲 + 状态栏
        self.log.document().setMaximumBlockCount(LOG_MAX_BLOCKS)
        self.statusBar().showMessage(f"示教器后端 {url} · 控制台 {console_url}")

        # 快捷键：Esc 急停 / F5 立即刷新 / F11 全屏（车间平板常用）
        QShortcut(QKeySequence("Esc"), self, activated=self.do_stop)
        QShortcut(QKeySequence("F5"), self, activated=self.refresh_status)
        QShortcut(QKeySequence("F11"), self, activated=self._toggle_fullscreen)

        # 窗口几何记忆（QSettings 持久化上次位置/大小）
        self._settings = QSettings("RTP", "QTTeachPendant")
        _geo = self._settings.value("geometry")
        if _geo:
            self.restoreGeometry(_geo)

        self._set_led(False)
        self._poll = QTimer(self)
        self._poll.setInterval(POLL_MS)
        self._poll.timeout.connect(self.refresh_status)
        self._poll.start()
        self.refresh_status()

    # -- 窗口行为 -----------------------------------------------------------
    def closeEvent(self, event):  # noqa: N802（Qt 命名约定）
        self._settings.setValue("geometry", self.saveGeometry())
        super().closeEvent(event)

    def _toggle_fullscreen(self):
        if self.isFullScreen():
            self.showNormal()
        else:
            self.showFullScreen()

    # -- 外观辅助 -----------------------------------------------------------
    def _set_led(self, on: bool, color: str = "#22c55e"):
        c = color if on else "#64748b"
        self.led.setStyleSheet(
            f"background:{c};border-radius:7px;border:1px solid #334155")
        self.led.setToolTip("伺服已使能" if on else "伺服未使能 / 未连接")

    def _log(self, msg: str, level: str = "INFO"):
        ts = datetime.now().strftime("%H:%M:%S")
        color = {"ERR": "#f87171", "WARN": "#fbbf24"}.get(level, "#a5b4cf")
        self.log.append(
            f'<span style="color:#64748b">[{ts}]</span> '
            f'<span style="color:{color}">{msg}</span>')

    def _log_result(self, prefix: str, data, err):
        self._log(f"{prefix}: {self._fmt(data, err)}", "ERR" if err else "INFO")

    # -- 操作 ---------------------------------------------------------------
    def do_servo(self, on: bool):
        self.backend.servo(
            on, lambda d, e: self._log_result(f"servo {'ON' if on else 'OFF'}", d, e))

    def do_jog(self, joint: int, direction: int, log: bool = True):
        step = self.current_step()
        sign = "+" if direction > 0 else "-"

        def _cb(d, e):
            if e:
                self._log_result(f"jog J{joint}{sign}", d, e)   # 失败必记
            elif log:
                self._log(f"jog J{joint}{sign} ×{step}° ok")

        self.backend.jog(joint, direction, _cb, step=step)

    def do_stop(self):
        self.backend.stop(lambda d, e: self._log_result("stop", d, e))

    def do_reset(self):
        self.backend.reset(lambda d, e: self._log_result("reset", d, e))

    def do_home(self):
        self.backend.home(lambda d, e: self._log_result("home", d, e))

    def do_move_absolute(self):
        try:
            target = [float(inp.text()) for inp in self.target_inputs]
        except ValueError:
            self._log("Move: 目标角度需为数字", "WARN")
            return
        self.backend.move_absolute(
            target, lambda d, e: self._log_result("move_abs", d, e))

    def do_read_current(self):
        """把当前实际关节角填进 Move Absolute 输入框（示教点位常用）。"""
        if not self._last_joints:
            self._log("尚无关节数据", "WARN")
            return
        for i, v in enumerate(self._last_joints[:DOF]):
            self.target_inputs[i].setText(f"{v:.2f}")
        self._log("已读取当前关节角 → Move Absolute")

    # -- Jog 交互：按住连发 -------------------------------------------------
    def current_step(self) -> float:
        try:
            return float(self.cmb_step.currentText())
        except (ValueError, AttributeError):
            return JOG_STEP_DEG

    def _bind_jog(self, btn, joint: int, direction: int):
        btn.pressed.connect(lambda j=joint, d=direction: self._jog_press(j, d))
        btn.released.connect(self._jog_release)
        btn.setToolTip(f"按住连续点动 J{joint}（步长可切）")

    def _jog_press(self, joint: int, direction: int):
        self.do_jog(joint, direction)
        self._jog_joint, self._jog_dir = joint, direction
        self._jog_repeat.setInterval(JOG_REPEAT_FIRST_MS)
        self._jog_repeat.start()

    def _on_jog_repeat(self):
        self._jog_repeat.setInterval(JOG_REPEAT_MS)
        if self._jog_joint is not None:
            self.do_jog(self._jog_joint, self._jog_dir, log=False)

    def _jog_release(self):
        self._jog_repeat.stop()
        if self._jog_joint is not None:
            self._log(f"jog J{self._jog_joint} 松开停止")
        self._jog_joint = None

    @staticmethod
    def _fmt(data, err):
        if err:
            return f"ERR {err}"
        return json.dumps(data, ensure_ascii=False)

    # -- 轮询刷新 -----------------------------------------------------------
    def refresh_status(self):
        """周期刷新；上一轮未回包则跳过，避免慢网络下请求堆积/状态抖动。"""
        if self._inflight:
            return
        self._t0 = time.perf_counter()
        self._inflight = 2
        self.backend.status(self._on_status)
        self.backend.alarm(self._on_alarm)

    def _settle(self):
        self._inflight = max(0, self._inflight - 1)

    def _on_status(self, d, e):
        try:
            if e:
                self._err_streak += 1
                self._set_led(False, "#ef4444")
                self.title.setText("示教器（连接异常）")
                self.lbl_conn.setText(f"{self.url} · 连接失败 ×{self._err_streak}")
                self.lbl_conn.setStyleSheet("color:#f87171;font-weight:600")
                return
            self._err_streak = 0
            lat = (time.perf_counter() - self._t0) * 1000
            self.title.setText(
                f"示教器 · {d.get('motion_state', '?')} · "
                f"{'ENABLED' if d.get('enabled') else 'disabled'}")
            self._set_led(bool(d.get("enabled")),
                          "#ef4444" if d.get("error") else "#22c55e")
            joints = d.get("joints") or []
            self._last_joints = list(joints)
            for i, lab in enumerate(self.joint_labels):
                lab.setText(f"{joints[i]:.2f}" if i < len(joints) else "?")
            can_jog = bool(d.get("enabled")) and not d.get("error")
            for b in self.jog_buttons:
                b.setEnabled(can_jog)
            self.btn_move.setEnabled(can_jog)
            self.lbl_conn.setStyleSheet("color:#4ade80")
            self.lbl_conn.setText(f"{self.url} · {lat:.0f} ms")
        finally:
            self._settle()

    def _on_alarm(self, d, e):
        try:
            if e or not d or not d.get("error"):
                self.alarm_label.setText("无报警")
                self.alarm_label.setStyleSheet(
                    "padding:6px;border-radius:4px;background:#132a20;color:#4ade80")
                return
            msgs = "；".join(a.get("message", "") for a in d.get("alarms", []))
            code = d.get("alarms", [{}])[0].get("code", "?")
            self.alarm_label.setText(f"[{code}] {msgs}")
            self.alarm_label.setStyleSheet(
                "padding:6px;border-radius:4px;background:#3b1111;"
                "color:#fca5a5;font-weight:600")
        finally:
            self._settle()


def main() -> None:
    ap = argparse.ArgumentParser(description="PyQt5 桌面示教器面板")
    ap.add_argument("--url", default="http://127.0.0.1:58081",
                    help="teach_pendant REST 基址（真实模式）")
    ap.add_argument("--console-url", default="http://127.0.0.1:58090",
                    help="管理控制台基址（测试与曲线标签页内嵌网页）")
    ap.add_argument("--mock", action="store_true",
                    help="离线模式：内置仿真控制器，无需 agent")
    ap.add_argument("--fullscreen", action="store_true",
                    help="以全屏启动（车间平板场景，F11 可切换）")
    args = ap.parse_args()

    # 高分屏适配：必须在 QApplication 构造前设置，否则 4K/缩放屏会模糊
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)
    try:  # Qt 5.14+ 才有取整策略接口
        QGuiApplication.setHighDpiScaleFactorRoundingPolicy(
            Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    except Exception:  # noqa: BLE001
        pass

    app = QApplication(sys.argv)
    app.setApplicationName("RTP Teach Pendant")
    app.setFont(QFont("Segoe UI", 10))
    win = QTTeachPendant(mock=args.mock, url=args.url,
                         console_url=args.console_url)
    if args.fullscreen:
        win.showFullScreen()
    else:
        win.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
