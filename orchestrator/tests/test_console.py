"""管理控制台只读 API 测试（P3：frontend/console）。

断言不依赖硬编码的用例总数（随项目增长），只锁定结构、关键分组
与数据一致性；"当前基线 74 项"由 README/design 维护。
"""
from fastapi.testclient import TestClient

from app.console import app

client = TestClient(app)


def test_console_cases_list_structure():
    r = client.get("/api/cases")
    assert r.status_code == 200
    d = r.json()
    assert d["total"] >= 70          # 当前基线 74 项
    names = {c["name"] for c in d["cases"]}
    # 各阶段代表性用例必须在册
    for key in ("test_emergency_reset_allows_relaunch", "test_standard_enable_sequence",
                "test_estop_interlock_and_controller_link", "test_tp_servo_on_controls_controller",
                "test_position_accuracy"):
        assert key in names, key
    groups = {c["group"] for c in d["cases"]}
    assert len(groups) >= 10         # 10 个业务分组


def test_console_runs_list_structure():
    r = client.get("/api/runs")
    assert r.status_code == 200
    d = r.json()
    assert d["count"] >= 1
    run = d["runs"][0]
    for k in ("run_id", "started_at", "profile", "cycle_hz",
              "passed", "failed", "total", "status"):
        assert k in run, k
    assert run["passed"] + run["failed"] == run["total"]
    assert run["total"] >= 70


def test_console_defects_view_when_green():
    r = client.get("/api/defects")
    assert r.status_code == 200
    d = r.json()
    assert d["latest_run"] is not None
    assert isinstance(d["open"], list)
    assert isinstance(d["closed"], list)
    # 最近运行全绿（基线），open 应为空且 note 说明闭环
    assert d["open"] == []
    assert "闭环" in d["note"] or "全绿" in d["note"]


def test_console_summary_consistency():
    r = client.get("/api/summary")
    assert r.status_code == 200
    d = r.json()
    st = d["stats"]
    cases = client.get("/api/cases").json()
    assert st["total_cases"] == cases["total"]
    assert st["last_total"] == st["last_passed"] + st["last_failed"]
    assert st["last_total"] == d["latest_run"]["total"]
    assert d["defects"]["open"] == []
    # 分组统计与用例清单一致
    by_group = st["by_group"]
    assert sum(by_group.values()) == st["total_cases"]
