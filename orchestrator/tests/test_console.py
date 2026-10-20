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
    """替身进程：poll() 返回预设返回码；pid=None 保证中止路径不会杀真实进程。"""

    def __init__(self, rc):
        self._rc = rc
        self.pid = None
        self.terminated = False
        self.killed = False

    def poll(self):
        return self._rc

    def terminate(self):
        self.terminated = True
        if self._rc is None:
            self._rc = -15

    def kill(self):
        self.killed = True
        self._rc = -9


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


def test_console_run_waveform_endpoint(console_env):
    # 预置一条带逐周期波形的运行记录
    wf_dir = console_env / "run_20260923_wf"
    wf_dir.mkdir(parents=True, exist_ok=True)
    ctx = RunContext(
        profile_name="maira_sim", profile_model="MAiRA-SIM-7DOF",
        controller="Simulation", cycle_hz=500, port=50051,
        started_at="2026-09-23T00:05:00")
    ctx.add("test_waveform_500hz_single", "PASS", "ok", 0.5)
    write_run(ctx, wf_dir, waveforms=[{
        "name": "test_waveform_500hz_single", "cycle_hz": 500, "slaves": "single",
        "interval_us": [2000.0 + i for i in range(10)],
        "processing_us": [120.0 + (i % 3) * 5 for i in range(10)],
        "jitter_stats": "min=2000 max=2009 mean=2004 std=3 (us)",
        "latency_stats": "min=120 max=130 mean=124 std=4 (us)",
        "overrun_cycles": 0,
    }])

    r = client.get("/api/runs/run_20260923_wf/waveform")
    assert r.status_code == 200
    d = r.json()
    assert d["run_id"] == "run_20260923_wf"
    assert len(d["curves"]) == 1
    cv = d["curves"][0]
    assert cv["cycle_hz"] == 500
    assert cv["slaves"] == "single"
    assert len(cv["interval_us"]) == 10
    assert len(cv["processing_us"]) == 10
    assert cv["jitter_stats"] and cv["latency_stats"]

    # 有运行但无波形（如 P0 分组）→ 空列表 200
    r2 = client.get("/api/runs/run_20260923_001/waveform")
    assert r2.status_code == 200 and r2.json()["curves"] == []
    # 运行不存在 → 404
    assert client.get("/api/runs/run_nope/waveform").status_code == 404


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


def test_console_run_trajectory_api(console_env, monkeypatch):
    import csv
    # 预置一条带轨迹采样的运行记录（MoveAbsolute 7 轴 x 12 点）
    td = console_env / "run_20260923_traj" / "trajectory.csv"
    td.parent.mkdir(parents=True, exist_ok=True)
    head = "timestamp_ns," + ",".join(["q%d" % i for i in range(7)] + ["dq%d" % i for i in range(7)] + ["ddq%d" % i for i in range(7)])
    with td.open("w", encoding="utf-8", newline="") as f:
        f.write(head + "\n")
        base = 1000000000000
        for k in range(12):
            row = [str(base + k * 1000000)] + [str(float(k)) for _ in range(21)]
            f.write(",".join(row) + "\n")

    r = client.get("/api/runs/run_20260923_traj/trajectory?max_points=8")
    assert r.status_code == 200
    d = r.json()
    assert d["sample_count"] == 12
    assert d["max_points"] == 8           # 已抽稀
    assert len(d["time_ms"]) == 8
    for k in ("q", "dq", "ddq"):
        assert len(d[k]) == 7 and len(d[k][0]) == 8
    assert d["time_ms"][0] == 0.0         # 相对起点

    # 无轨迹采样 → 404
    assert client.get("/api/runs/run_20260923_001/trajectory").status_code == 404
    assert client.get("/api/runs/run_nope/trajectory").status_code == 404


def test_console_waveforms_svg_endpoint(console_env, monkeypatch):
    # 注入 WAVEFORMS 目录（独立于 REPORTS，避免污染真实 reports/waveforms）
    wf = console_env / "waveforms"
    wf.mkdir(parents=True, exist_ok=True)
    (wf / "jitter_1000hz.svg").write_text('<svg xmlns="http://www.w3.org/2000/svg"></svg>', encoding="utf-8")
    monkeypatch.setattr(console, "WAVEFORMS", wf)

    r = client.get("/api/waveforms/jitter_1000hz.svg")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("image/svg+xml")

    assert client.get("/api/waveforms/nope.svg").status_code == 404
    # 路径穿越防护
    assert client.get("/api/waveforms/..%2Fsecret.svg").status_code in (400, 404)


# ---------------------------------------------------------------------------
# 实时曲线（运行中 SSE 流式推送）：_active_stream_gen 生成器语义测试
# ---------------------------------------------------------------------------
def _seg(case: str, n: int = 6) -> str:
    import json
    return json.dumps({"case": case, "n": n,
                       "t_ms": [0.0, 10.0, 20.0, 30.0, 40.0, 50.0],
                       "q": [[float(i)] * n for i in range(7)]}, ensure_ascii=False)


def test_active_stream_pushes_segments_then_ends(tmp_path):
    """运行中增量推送新段；运行结束（is_active=False）后流收尾。"""
    live = tmp_path / "active.live.jsonl"
    with live.open("a", encoding="utf-8") as f:
        f.write(_seg("test_a") + "\n")
        f.write(_seg("test_b") + "\n")
    active = {"v": True}
    gen = console._active_stream_gen(live, lambda: active["v"])

    ev = []
    for _ in range(2):                       # 已有两段先推
        ev.append(next(gen))
    assert "test_a" in ev[0] and "test_b" in ev[1]

    with live.open("a", encoding="utf-8") as f:   # 运行中追加新段
        f.write(_seg("test_c") + "\n")
    ev.append(next(gen))
    assert "test_c" in ev[-1]

    active["v"] = False                       # 运行结束 → 流收尾
    with pytest.raises(StopIteration):
        next(gen)


def test_active_stream_no_data_ends_when_inactive(tmp_path):
    """无数据（live 文件未创建）且无运行 → 流尽快结束，不无限等待。"""
    live = tmp_path / "nope.jsonl"
    active = {"v": False}
    gen = console._active_stream_gen(live, lambda: active["v"])
    with pytest.raises(StopIteration):
        next(gen)


def test_active_stream_http_event_stream(console_env, monkeypatch):
    """HTTP 层：SSE 端点返回 event-stream，完整响应含各段 data 事件。"""
    live = console_env / "active.live.jsonl"
    with live.open("a", encoding="utf-8") as f:
        f.write(_seg("test_a") + "\n")
        f.write(_seg("test_b") + "\n")
    monkeypatch.setattr(console, "LIVE_ACTIVE", live)
    monkeypatch.setattr(console, "_ACTIVE_RUN", None)   # 无运行 → 推送完即收尾

    r = client.get("/api/runs/active/stream")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/event-stream")
    assert r.text.count("data: {") == 2
    assert "test_a" in r.text and "test_b" in r.text


# ---------------------------------------------------------------------------
# 手动示波器（P3）：示教器 jog 采样期间 SSE 保持活跃 + /api/manual/active
# ---------------------------------------------------------------------------
def test_console_manual_active_endpoint(console_env, monkeypatch):
    """手动活跃标记：无标记 false；touch 后 true；mtime 过期后自动失效。"""
    import os
    import time
    flag = console_env / "manual.live.active"
    monkeypatch.setattr(console, "MANUAL_ACTIVE", flag)

    assert client.get("/api/manual/active").json()["active"] is False
    flag.write_text("1", encoding="utf-8")
    assert client.get("/api/manual/active").json()["active"] is True
    # 标记过期（进程异常退出的残留场景）→ 判定为 inactive，SSE 不会永不收尾
    old = time.time() - 30
    os.utime(flag, (old, old))
    assert client.get("/api/manual/active").json()["active"] is False


def test_active_stream_is_active_includes_manual(console_env, monkeypatch):
    """SSE 活跃判定 = 有运行 OR 手动监控活跃（残留标记随新鲜度失效）。"""
    import os
    import time
    flag = console_env / "manual.live.active"
    monkeypatch.setattr(console, "MANUAL_ACTIVE", flag)
    console._ACTIVE_RUN = None
    try:
        assert console._manual_active() is False
        flag.write_text("1", encoding="utf-8")
        assert console._manual_active() is True
        old = time.time() - 30
        os.utime(flag, (old, old))
        assert console._manual_active() is False
    finally:
        console._ACTIVE_RUN = None


# ---------------- 运行治理：并发互斥 / 中止 / 卡死自动中止 ----------------

def _begin_fake_run(console, monkeypatch, tmp_path, selector="全量"):
    """起一个假运行（不拉真实子进程），并把 live 流/锁都指向临时目录。"""
    import time
    console._ACTIVE_RUN = None
    monkeypatch.setattr(console, "LIVE_ACTIVE", tmp_path / "active.live.jsonl")
    console._ACTIVE_RUN = {
        "proc": _FakeProc(None), "pid": None,
        "started_at": "2026-09-23T00:06:00", "selector": selector,
        # 心跳起点取当前时间，否则会被判定为"长时间无进展"
        "last_sig": (-1, -1), "last_progress": time.time(),
    }


def test_console_run_status_reports_heartbeat(console_env, monkeypatch):
    """运行中的 status 必须带心跳字段（idle_seconds / stall_timeout_sec）。"""
    import app.console as console
    _begin_fake_run(console, monkeypatch, console_env)
    try:
        s = client.get("/api/runs/status").json()
        assert s["running"] is True
        assert s["stalled"] is False
        assert "idle_seconds" in s and s["idle_seconds"] >= 0
        assert s["stall_timeout_sec"] == console.STALL_TIMEOUT_SEC
    finally:
        console._ACTIVE_RUN = None


def test_console_run_rejects_concurrent_with_active_run(console_env, monkeypatch):
    """已有运行 → 新触发被拒（409），且错误信息可直接指引到 cancel 接口。"""
    import app.console as console
    _begin_fake_run(console, monkeypatch, console_env)
    try:
        r = client.post("/api/runs", json={})
        assert r.status_code == 409
        assert "已有运行在进行中" in r.json()["error"]
        assert "/api/runs/cancel" in r.json()["error"]
    finally:
        console._ACTIVE_RUN = None


def test_console_run_rejects_stray_lock(console_env, monkeypatch):
    """跨进程残留锁：本实例无运行但锁里 pid 仍存活 → 拒绝触发（防双 pytest 抢控制器）。"""
    import os
    import app.console as console
    console._ACTIVE_RUN = None
    lock = console_env / ".run.lock"
    lock.write_text('{"pid": %d, "started_at": "2026-09-23T00:07:00", "selector": "全量"}'
                    % os.getpid(), encoding="utf-8")
    r = client.post("/api/runs", json={})
    assert r.status_code == 409
    assert "未受本实例管理" in r.json()["error"]


def test_console_run_cancel_aborts_and_records(console_env, monkeypatch):
    """中止：进程被终止 + 落一条 ABORTED 记录 + 运行态与锁被清理。"""
    import json
    import app.console as console
    _begin_fake_run(console, monkeypatch, console_env)
    r = client.post("/api/runs/cancel", json={"reason": "测试中止"})
    assert r.status_code == 200
    d = r.json()
    assert d["cancelled"] is True
    assert d["reason"] == "测试中止"
    # 中止记录落盘，且视图判为 FAIL（卡死/中止不会静默消失）
    run_id = d["aborted_run"]
    rec = json.loads((console_env / run_id / "result.json").read_text(encoding="utf-8"))
    assert rec["aborted"] is True
    assert rec["summary"] == {"passed": 0, "failed": 1, "total": 1}
    assert console._ACTIVE_RUN is None
    # 锁被释放（写回 pid=0，不再视为有运行中任务）
    lock = json.loads((console_env / ".run.lock").read_text(encoding="utf-8"))
    assert lock["pid"] == 0
    assert console._read_run_lock() is None
    # 中止记录进入运行历史
    hist = client.get("/api/runs").json()
    assert any(x["run_id"] == run_id and x["status"] == "FAIL" for x in hist["runs"])


def test_console_run_cancel_when_idle(console_env, monkeypatch):
    """没有运行中的任务时调用中止 → 409（明确语义，不静默成功）。"""
    import app.console as console
    console._ACTIVE_RUN = None
    r = client.post("/api/runs/cancel", json={})
    assert r.status_code == 409
    assert r.json()["cancelled"] is False


def test_console_run_status_auto_cancels_on_stall(console_env, monkeypatch):
    """卡死检测：实时流长时间无进展 → status 自动中止并标记失败。"""
    import app.console as console
    _begin_fake_run(console, monkeypatch, console_env)
    # 模拟"已经 500s 没有任何新数据"（超过默认 180s 阈值）
    import time
    console._ACTIVE_RUN["last_progress"] = time.time() - 500
    s = client.get("/api/runs/status").json()
    assert s["running"] is False
    assert s["stalled"] is True
    assert s["auto_cancelled"] is True
    assert s["stalled_seconds"] > 0
    assert s["aborted_run"]
    rec = (console_env / s["aborted_run"] / "result.json").read_text(encoding="utf-8")
    assert "卡死" in rec
    assert console._ACTIVE_RUN is None
