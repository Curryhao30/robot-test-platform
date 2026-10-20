"""Qt 桌面面板页签结构测试（独立文件，避免与 QtWebEngine import 顺序冲突）。

Qt 规则：`QtWebEngineWidgets` 必须在任何 QCoreApplication 创建之前导入。
本文件按字母序在 test_qt_panel.py 之后被 pytest 收集 import，但收集阶段
conftest / 契约测试尚未创建 QCoreApplication，因此模块级 import qt_panel
是安全的；运行时即便 app 已存在，模块缓存也已生效。

页签结构：示教器（自绘 HMI）/ Web 示教器（内嵌 58081，含实时曲线）/
测试与曲线（内嵌 58090 控制台）。WebEngine 在 CI 无 GUI 环境不可用时自动 skip。
"""

from __future__ import annotations

import pathlib
import sys

ORCH = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ORCH))

import pytest

try:
    from PyQt5.QtWidgets import QApplication, QWidget
    from teach_pendant import qt_panel as tp_mod
    from teach_pendant.qt_panel import QTTeachPendant
    _HAS_PANEL = True
    # Qt 规则：QWebEngineWidgets 必须先于任何 QCoreApplication import（上面已 import）。
    # 这里在模块级创建 QApplication，使全会话（含 test_qt_panel.py 契约测试）复用，
    # 避免"已有 QCoreApplication 再建 QApplication"导致的 Qt abort。
    _APP = QApplication([])
except Exception:  # noqa: BLE001  (无 PyQt5 / 缺运行库的环境)
    _HAS_PANEL = False


class _FakeWebView(QWidget):
    """QWebEngineView 的 stub 替身：只记录 URL，不启动 QtWebEngine 渲染进程。

    QtWebEngine 在无真实桌面/GPU 会话（CI、agent 后台 shell）下构造即崩溃
    （0xC0000409），而本测试只关心页签壳结构，无需真实渲染。
    继承 QWidget 以满足 insertTab 的 QWidget 类型约束。
    """

    def __init__(self):
        super().__init__()
        self._url = None

    def setUrl(self, url):
        self._url = url

    def url(self):
        return self._url


def test_panel_has_three_tabs():
    """Qt 面板三页签：自绘示教器 / Web 示教器（58081 含实时曲线）/ 测试与曲线（58090）。

    以 stub 替换 QWebEngineView，验证 tabWidget 页签结构与 URL 指向；
    Web 示教器页签指向 58081（Jog 页内嵌实时曲线）。
    """
    if not _HAS_PANEL:
        pytest.skip("PyQt5 不可用")
    orig = tp_mod.QWebEngineView
    tp_mod.QWebEngineView = _FakeWebView
    try:
        win = QTTeachPendant(backend=None, mock=True,
                             url="http://127.0.0.1:58081",
                             console_url="http://127.0.0.1:58090")
    finally:
        tp_mod.QWebEngineView = orig
    try:
        assert win.tabWidget.count() == 3
        titles = [win.tabWidget.tabText(i) for i in range(3)]
        assert titles == ["示教器", "Web 示教器", "测试与曲线"], titles
        assert isinstance(win.tp_web, _FakeWebView), "缺少 Web 示教器页签视图"
        assert win.tp_web._url.toString().startswith("http://127.0.0.1:58081")
    finally:
        win.close()
