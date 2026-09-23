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
    # summary 携带运行历史（前端运行历史区依赖）
    assert len(d["runs"]) == 2


# ---------------- 可操作版：触发运行 / 状态 / 明细 / 缺陷人工闭环 ----------------

class _FakeProc:
    def __init__(self, rc):
        self._rc = rc

    def poll(self):
        return self._rc


def test_console_run_trigger_and_status(console_env, monkeypatch):
    import app.console as console

    def fake_start(group=None):
        console._ACTIVE_RUN = {
            "proc": _FakeProc(None),      # poll() -> None → 运行中
            "pid": 999,
            "started_at": "2026-09-23T00:03:00",
            "selector": group or "全量",
        }
        return {"started": True, "pid": 999, "started_at": "2026-09-23T00:03:00",
                "selector": group or "全量"}

    monkeypatch.setattr(console, "_start_test_run", fake_start)
    r = client.post("/api/runs", json={})
    assert r.status_code == 200
    assert r.json()["started"] is True

    s = client.get("/api/runs/status").json()
    assert s["running"] is True
    assert s["selector"] == "全量"

    # 进程结束 → running=False 且返回最新 run
    console._ACTIVE_RUN["proc"] = _FakeProc(0)
    s2 = client.get("/api/runs/status").json()
    assert s2["running"] is False
    assert s2["latest_run"]["run_id"] == "run_20260923_002"
    assert console._ACTIVE_RUN is None


def test_console_run_trigger_group_and_validation(console_env, monkeypatch):
    import app.console as console

    def fake_start(group=None):
        console._ACTIVE_RUN = {"proc": _FakeProc(None), "pid": 1000,
                               "started_at": "2026-09-23T00:04:00", "selector": group}
        return {"started": True, "selector": group}

    monkeypatch.setattr(console, "_start_test_run", fake_start)
    r = client.post("/api/runs", json={"group": "安全联锁"})
    assert r.status_code == 200 and r.json()["started"]
    # 未知分组 → 400
    r2 = client.post("/api/runs", json={"group": "不存在分组"})
    assert r2.status_code == 400


def test_console_run_rejects_concurrent(console_env, monkeypatch):
    import app.console as console
    monkeypatch.setattr(console, "_start_test_run",
                        lambda group=None: (_ for _ in ()).throw(RuntimeError("已有运行在进行中")))
    r = client.post("/api/runs", json={})
    assert r.status_code == 409
    assert "已有运行" in r.json()["error"]


def test_console_run_detail(console_env):
    r = client.get("/api/runs/run_20260923_002")
    assert r.status_code == 200
    d = r.json()
    assert d["run_id"] == "run_20260923_002"
    assert d["total"] == 2
    assert any(c["name"] == "test_fault_x" and c["status"] == "FAIL" for c in d["cases"])
    # 不存在 → 404
    assert client.get("/api/runs/run_nope").status_code == 404


def test_console_defect_manual_close_reopen(console_env):
    # 初始：latest=002 含 FAIL → BUG-002 OPEN
    d0 = client.get("/api/defects").json()
    assert any(x["id"] == "BUG-002" and not x.get("manual") for x in d0["open"])

    r = client.post("/api/defects/BUG-002/close", json={"reason": "人工关闭（回归确认）"})
    assert r.status_code == 200
    d1 = r.json()["defects"]
    assert d1["open"] == []
    hit = next(x for x in d1["closed"] if x["id"] == "BUG-002")
    assert hit["manual"] is True and hit["reason"].startswith("人工关闭")

    # 重开 → 回到 open（manual 标记）
    r2 = client.post("/api/defects/BUG-002/reopen", json={})
    d2 = r2.json()["defects"]
    hit2 = next(x for x in d2["open"] if x["id"] == "BUG-002")
    assert hit2["manual"] is True

    # 人工状态落在注入的 REPORTS 下，不污染真实 reports
    assert (console_env / "defect_state.json").is_file()
