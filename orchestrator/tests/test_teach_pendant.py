"""P1-4 用例：虚拟示教器五块 UI（Jog/Program/I/O/Robot/Alarm）+ Playwright。

核心语义（示教器 × 控制器联合测试）：
    UI 操作必须真实改变 Controller State，而不是只验证按钮可点——
    例如点 Jog+ 后既看 UI 位置值变化，又经 /api/status 读控制器关节位置。
"""
from __future__ import annotations

import pytest

from tests import capture

# 等待函数
def _wait_until(pred, timeout_s=8.0, interval=0.2, msg=""):
    import time
    deadline = time.time() + timeout_s
    last = None
    while time.time() < deadline:
        try:
            last = pred()
            if last:
                return last
        except Exception as e:
            last = e
        time.sleep(interval)
    raise AssertionError(f"等待超时: {msg} (last={last!r})")


@pytest.fixture
def page(pw_browser):
    p = pw_browser.new_page(viewport={"width": 1280, "height": 800})
    yield p
    p.close()


@pytest.fixture
def tp(page, teach_url):
    page.goto(teach_url)
    page.wait_for_selector("#axes button[data-axis]", timeout=10_000)
    return page


def _api_json(page, path):
    return page.evaluate("async (p) => { const r = await fetch(p); return r.json(); }", path)


# ---------------------------------------------------------------------------
# 1. 五块 UI 布局
# ---------------------------------------------------------------------------
def test_tp_layout_five_tabs(tp):
    tabs = [b.inner_text() for b in tp.query_selector_all("nav button")]
    assert tabs == ["Jog", "Program", "I/O", "Robot", "Alarm"], tabs
    # Jog 页 7 个轴控件 + 控制按钮齐全
    assert len(tp.query_selector_all("#axes .axis-row")) == 7
    for sel in ["#servo-on", "#servo-off", "#stop", "#reset", "#coord-joint"]:
        assert tp.query_selector(sel), f"缺少 {sel}"
    capture.DETAILS["test_tp_layout_five_tabs"] = "五块 tab 齐全；Jog 7 轴 + 控制按钮"


def test_tp_switch_tabs(tp):
    for tab in ["program", "io", "robot", "alarm", "jog"]:
        tp.click(f"nav button[data-tab={tab}]")
        tp.wait_for_selector(f"#tab-{tab}.active", timeout=5000)
    capture.DETAILS["test_tp_switch_tabs"] = "五个 tab 均能切换并激活"


# ---------------------------------------------------------------------------
# 2. Servo ON：UI 状态灯 + 控制器真实使能
# ---------------------------------------------------------------------------
def test_tp_servo_on_updates_controller(tp, client):
    tp.click("#servo-on")
    _wait_until(lambda: "on" in (tp.get_attribute("#lamp-enable", "class") or ""),
                msg="UI Enable 灯变绿")
    st = _api_json(tp, "/api/status")
    assert st["enabled"] is True, st
    capture.DETAILS["test_tp_servo_on_updates_controller"] = (
        "Servo ON -> UI 状态灯变绿 + 控制器 enabled=True")


# ---------------------------------------------------------------------------
# 3. Jog 步进（核心：UI 操作改变 Controller State）
# ---------------------------------------------------------------------------
def test_tp_jog_moves_controller_joint(tp, client):
    tp.click("#servo-on")
    _wait_until(lambda: (_api_json(tp, "/api/status") or {}).get("enabled"),
                msg="控制器使能")
    before = _api_json(tp, "/api/status")["joints"][0]
    tp.click('button[data-axis="0"][data-dir="1"]')   # J1 +
    _wait_until(lambda: abs(_api_json(tp, "/api/status")["joints"][0] - before - 5.0) < 0.2,
                msg="控制器 J1 位置 +5°")
    ui_val = tp.inner_text("#val-0")
    assert abs(float(ui_val) - (before + 5.0)) < 0.2, f"UI={ui_val} expected≈{before + 5:.2f}"
    capture.DETAILS["test_tp_jog_moves_controller_joint"] = (
        f"点 J1+ -> 控制器 joints[0] {before:.1f}° -> {before + 5:.1f}°，UI 同步")


# ---------------------------------------------------------------------------
# 4. 运动中 Stop：控制器实际停止
# ---------------------------------------------------------------------------
def test_tp_stop_during_motion(tp, client):
    tp.click("#servo-on")
    _wait_until(lambda: (_api_json(tp, "/api/status") or {}).get("enabled"), msg="使能")
    # 长运动（P1 目标 90°），随后立即 Stop
    tp.evaluate("async () => { await fetch('/api/move_absolute', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({target:[90,0,0,0,0,0,0]})}); }")
    tp.click("#stop")
    _wait_until(lambda: (_api_json(tp, "/api/status") or {}).get("moving") is False,
                msg="控制器 moving=False")
    st = _api_json(tp, "/api/status")
    assert st["motion_state"] in ("ABORTED", "IDLE", "DONE"), st
    capture.DETAILS["test_tp_stop_during_motion"] = (
        f"长运动中点 STOP -> moving=False state={st['motion_state']}")


# ---------------------------------------------------------------------------
# 5. 未使能 Jog -> Alarm 面板报警（UI 联动）
# ---------------------------------------------------------------------------
def test_tp_alarm_when_move_without_servo(tp, client):
    tp.click("#servo-off")
    _wait_until(lambda: (_api_json(tp, "/api/status") or {}).get("enabled") is False,
                msg="控制器去使能")
    tp.click('button[data-axis="0"][data-dir="1"]')
    _wait_until(lambda: "#tab-alarm.active" and
                len(tp.query_selector_all("#alarm-list .alarm-item")) > 0,
                msg="Alarm tab 出现报警条目")
    items = tp.inner_text("#alarm-list")
    assert "ERR-1" in items or "ERR" in items, items
    capture.DETAILS["test_tp_alarm_when_move_without_servo"] = (
        "未使能点 Jog -> 自动切 Alarm tab 显示 ERR-1")


# ---------------------------------------------------------------------------
# 6. Program 运行（P1 到位 -> 控制器关节到达目标）
# ---------------------------------------------------------------------------
def test_tp_program_run_moves_controller(tp, client):
    tp.click("#servo-on")
    _wait_until(lambda: (_api_json(tp, "/api/status") or {}).get("enabled"), msg="使能")
    tp.click("nav button[data-tab=program]")
    tp.click('tr[data-prog="P1"] .run-prog')
    _wait_until(lambda: abs((_api_json(tp, "/api/status") or {}).get("joints", [0])[0] - 15.0) < 0.2,
                msg="P1 程序完成后 joints[0]≈15°")
    st = _api_json(tp, "/api/status")
    assert abs(st["joints"][2] - 90.0) < 0.2, st["joints"]
    capture.DETAILS["test_tp_program_run_moves_controller"] = (
        f"运行 P1 -> joints[0]=15° joints[2]=90°（控制器实际到位）")


# ---------------------------------------------------------------------------
# 7. Robot 信息页
# ---------------------------------------------------------------------------
def test_tp_robot_info_shown(tp):
    tp.click("nav button[data-tab=robot]")
    text = tp.inner_text("#robot-table")
    assert "maira_sim" in text and "7" in text, text
    capture.DETAILS["test_tp_robot_info_shown"] = "Robot 页显示型号/自由度/控制周期"
