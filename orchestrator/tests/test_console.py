"""管理控制台只读 API 测试（P3：frontend/console）。

测试自包含：reports 数据在测试内用 app.report 构造临时运行记录
（monkeypatch console.REPORTS），不依赖本机/CI 既有历史；
用例清单扫描真实 tests 目录（AST，不执行）。
"""
import pathlib

import pytest
from fastapi.testclient import TestClient

import app.console as console
from app.console import app
from app.report import RunContext, write_run

client = TestClient(app)


def _write_run(reports_root: pathlib.Path, name: str, records: list[tuple[str, str, str]],
                started_at: str) -> None:
    ctx = RunContext(
        profile_name="maira_sim",
        profile_model="MAiRA-SIM-7DOF",
        controller="Simulation",
        cycle_hz=1000,
        port=50051,
        started_at=started_at,
    )
    for case, status, detail in records:
        ctx.add(case, status, detail, 0.01)
    run_dir = reports_root / name
    run_dir.mkdir(parents=True, exist_ok=True)
    write_run(ctx, run_dir)


GREEN = [
    ("test_dummy_a", "PASS", ""),
    ("test_dummy_b", "PASS", ""),
    ("test_dummy_c", "PASS", ""),
]


@pytest.fixture
def console_env(tmp_path, monkeypatch):
    """临时 REPORTS 目录，预置两条真实格式的运行记录（一绿一败）。"""
    _write_run(tmp_path, "run_20260923_001", GREEN, "2026-09-23T00:00:00")
    _write_run(tmp_path, "run_20260923_002",
               [("test_dummy_a", "PASS", ""), ("test_fault_x", "FAIL", "limit exceeded")],
               "2026-09-23T00:01:00")
    monkeypatch.setattr(console, "REPORTS", tmp_path)
    return tmp_path


def test_console_cases_list_structure():
    r = client.get("/api/cases")
    assert r.status_code == 200
    d = r.json()
    assert d["total"] >= 70          # 当前基线 78 项
    names = {c["name"] for c in d["cases"]}
    # 各阶段代表性用例必须在册
    for key in ("test_emergency_reset_allows_relaunch", "test_standard_enable_sequence",
                "test_estop_interlock_and_controller_link", "test_tp_servo_on_controls_controller",
                "test_position_accuracy"):
        assert key in names, key
    groups = {c["group"] for c in d["cases"]}
    assert len(groups) >= 11         # 11 个业务分组（含 Console）


def test_console_runs_list_structure(console_env):
    r = client.get("/api/runs")
    assert r.status_code == 200
    d = r.json()
    assert d["count"] == 2
    runs = d["runs"]
    assert runs[0]["run_id"] == "run_20260923_002"   # started_at 新者在前
    for run in runs:
        for k in ("run_id", "started_at", "profile", "cycle_hz",
                  "passed", "failed", "total", "status"):
            assert k in run, k
        assert run["passed"] + run["failed"] == run["total"]
    assert runs[0]["status"] == "FAIL"
    assert runs[1]["status"] == "PASS"


def test_console_defects_view_when_green(console_env):
    # 最新 run 含失败 → open 应含 1 项
    r = client.get("/api/defects")
    assert r.status_code == 200
    d = r.json()
    assert d["latest_run"] == "run_20260923_002"
    assert len(d["open"]) == 1
    assert d["open"][0]["case"] == "test_fault_x"
    assert d["open"][0]["status"] == "OPEN"
    assert d["open"][0]["id"] == "BUG-002"      # cases 中第 2 项

    # 追加一条回归全绿运行 → 原失败用例在最新运行中 PASS → 闭环
    _write_run(console.REPORTS, "run_20260923_003",
               [("test_dummy_a", "PASS", ""), ("test_fault_x", "PASS", "fixed")],
               "2026-09-23T00:02:00")
    r2 = client.get("/api/defects")
    d2 = r2.json()
    assert d2["latest_run"] == "run_20260923_003"
    assert d2["open"] == []
    assert len(d2["closed"]) == 1 and d2["closed"][0]["case"] == "test_fault_x"


def test_console_summary_consistency(console_env):
    r = client.get("/api/summary")
    assert r.status_code == 200
    d = r.json()
    st = d["stats"]
    cases = client.get("/api/cases").json()
    assert st["total_cases"] == cases["total"]
    assert st["last_total"] == st["last_passed"] + st["last_failed"]
    assert st["last_total"] == d["latest_run"]["total"]
    by_group = st["by_group"]
    assert sum(by_group.values()) == st["total_cases"]
    # 缺陷来自最新失败运行
    assert len(d["defects"]["open"]) == 1
