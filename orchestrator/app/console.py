"""管理控制台 API：用例清单 / 运行历史 / 缺陷视图 / 触发运行（可操作版）。

数据源全部来自真实产物，不引入数据库：
- 用例清单：扫描 orchestrator/tests/*.py 中 test_* 函数（AST 解析）
- 运行历史：reports/run_*/result.json（每次全量运行的机器可读结果）
- 缺陷视图：由最近运行中 FAIL 用例派生，支持人工关闭/重开（覆盖自动状态）
- 触发运行：后台子进程执行 pytest（RTP_WRITE_REPORT=1 → 新 run 自动落盘）

设计语义：BUG 由失败用例自动派生，回归通过自动闭环；
人工操作（手动关闭/重开）以 defect_state.json 覆盖自动状态，
对应"管理软件 BUG"岗位职责，且所有状态都可从真实产物回溯。
"""
from __future__ import annotations

import ast
import json
import os
import pathlib
import subprocess
import sys
import time

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

REPO = pathlib.Path(__file__).resolve().parents[2]
TESTS = REPO / "orchestrator" / "tests"
ORCHESTRATOR = REPO / "orchestrator"
REPORTS = REPO / "reports"
FRONTEND = REPO / "frontend"


def _defect_state_file() -> pathlib.Path:
    """人工缺陷状态文件（跟随 REPORTS，测试注入临时目录时不污染真实数据）。"""
    return REPORTS / "defect_state.json"

app = FastAPI(title="Robot Test Platform Console API", version="0.14.0-p3-ops")

# 测试文件 → 业务分组（与 docs/design.md 用例矩阵一致）
FILE_GROUP = {
    "test_p0_core.py": "P0 运动控制核心",
    "test_cia402.py": "CiA402 驱动状态机",
    "test_ethercat.py": "EtherCAT 虚拟总线",
    "test_teach_pendant.py": "虚拟示教器",
    "test_bus_fault.py": "总线异常注入",
    "test_waveform.py": "波形报告",
    "test_adapter.py": "Robot Adapter",
    "test_p2_soem.py": "SOEM 骨架",
    "test_safety.py": "安全联锁",
    "test_tp_protocol.py": "TP 协议",
    "test_console.py": "管理控制台",
}
GROUP_FILE = {g: f for f, g in FILE_GROUP.items()}

# 后台运行状态（同一时间只允许一个）
_ACTIVE_RUN: dict | None = None


class _FinishedProc:
    """替身进程对象：立即处于"已完成"状态（测试用 fake runner 可复用）。"""

    def __init__(self, returncode: int = 0):
        self._rc = returncode

    def poll(self):
        return self._rc


def _start_test_run(group: str | None = None) -> dict:
    """启动后台 pytest 子进程（全量或单分组文件）。

    生产实现：真实 subprocess；测试通过 monkeypatch 本函数替换。
    """
    global _ACTIVE_RUN
    if _ACTIVE_RUN is not None:
        raise RuntimeError("已有运行在进行中")
    cmd = [sys.executable, "-m", "pytest", "-q", "-p", "no:warnings"]
    if group:
        f = GROUP_FILE.get(group)
        if f is None:
            raise ValueError(f"未知分组: {group}")
        cmd.append("tests/" + f)
    env = dict(os.environ)
    env["RTP_WRITE_REPORT"] = "1"
    log = REPORTS / "manual_run.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("wb") as lf:
        proc = subprocess.Popen(cmd, cwd=str(ORCHESTRATOR), env=env,
                                stdout=lf, stderr=subprocess.STDOUT)
    _ACTIVE_RUN = {
        "proc": proc,
        "pid": proc.pid,
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "selector": group or "全量",
    }
    return {"started": True, **_ACTIVE_RUN, "proc": None}


def _scan_cases() -> list[dict]:
    """AST 扫描 tests/*.py 的 test_* 函数，返回用例清单（不执行）。"""
    cases: list[dict] = []
    for py in sorted(TESTS.glob("test_*.py")):
        try:
            tree = ast.parse(py.read_text(encoding="utf-8"))
        except (OSError, SyntaxError):
            continue
        group = FILE_GROUP.get(py.name, py.name)
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                    and node.name.startswith("test_"):
                cases.append({
                    "name": node.name,
                    "file": py.name,
                    "group": group,
                    "line": node.lineno,
                })
    return cases


def _iter_runs():
    if not REPORTS.is_dir():
        return []
    runs = []
    for d in REPORTS.glob("run_*"):
        rj = d / "result.json"
        if not rj.is_file():
            continue
        try:
            data = json.loads(rj.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        runs.append({"dir": d.name, "data": data})
    runs.sort(key=lambda r: r["data"].get("started_at", ""), reverse=True)
    return runs


def _run_view(run: dict) -> dict:
    data = run["data"]
    summary = data.get("summary", {})
    return {
        "run_id": run["dir"],
        "started_at": data.get("started_at", ""),
        "profile": (data.get("profile") or {}).get("name", ""),
        "cycle_hz": data.get("cycle_hz", 0),
        "passed": summary.get("passed", 0),
        "failed": summary.get("failed", 0),
        "total": summary.get("total", 0),
        "status": "PASS" if summary.get("failed", 0) == 0 and summary.get("total", 0) > 0 else "FAIL",
    }


def _load_defect_state() -> dict:
    """人工缺陷状态：{"BUG-001": {"action":"CLOSE","reason":"...","ts":"..."}}。"""
    f = _defect_state_file()
    if not f.is_file():
        return {}
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_defect_state(state: dict) -> None:
    f = _defect_state_file()
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(
        json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def _defects_view(run: dict | None) -> dict:
    """缺陷视图：自动派生 + 人工覆盖。

    自动语义：
    - 最新运行中 FAIL 的用例 → OPEN 缺陷（id 按最新运行 cases 序号派生）；
    - 历史上 FAIL 过、且最新运行中 PASS 的用例 → CLOSED（回归闭环）；
    - 人工操作（CLOSE/REOPEN）写入 defect_state.json，覆盖自动状态，
      前端显示 MANUAL 标记。
    """
    if run is None:
        return {"latest_run": None, "open": [], "closed": [], "note": "尚无运行记录"}
    data = run["data"]
    cases = data.get("cases", [])
    # 历史失败记录（最新 run 之前所有 run 中出现过的 FAIL 用例名）
    failed_history: set[str] = set()
    for older in _iter_runs()[1:]:
        for c in older["data"].get("cases", []):
            if c.get("status") != "PASS":
                failed_history.add(c.get("name", ""))

    open_defects = []
    closed = []
    for i, c in enumerate(cases, 1):
        name = c.get("name", "")
        if c.get("status") != "PASS":
            open_defects.append({
                "id": f"BUG-{i:03d}",
                "case": name,
                "detail": c.get("detail", "") or "见运行日志",
                "status": "OPEN",
                "found_in": run["dir"],
            })
        elif name in failed_history:
            closed.append({
                "id": f"BUG-{i:03d}",
                "case": name,
                "detail": "回归通过",
                "status": "CLOSED",
                "closed_in": run["dir"],
            })

    # 人工状态覆盖（key = 用例名，避免 BUG 序号随运行轮次漂移）
    manual = _load_defect_state()
    for case_name, st in manual.items():
        action = st.get("action")
        if action == "CLOSE":
            hit = next((x for x in open_defects if x["case"] == case_name), None)
            if hit:
                open_defects = [x for x in open_defects if x["case"] != case_name]
                hit["status"] = "CLOSED"
                hit["manual"] = True
                hit["reason"] = st.get("reason", "")
                closed.append(hit)
        elif action == "REOPEN":
            hit = next((x for x in closed if x["case"] == case_name), None)
            if hit:
                closed = [x for x in closed if x["case"] != case_name]
                hit["status"] = "OPEN"
                hit["manual"] = True
                hit["reason"] = st.get("reason", "")
                open_defects.append(hit)
            else:
                # 人工确认仍未闭环（与自动 OPEN 一致，仅加 MANUAL 标记）
                hit = next((x for x in open_defects if x["case"] == case_name), None)
                if hit and not hit.get("manual"):
                    hit["manual"] = True
                    hit["reason"] = st.get("reason", "")

    note = "最近运行全绿，无未闭环缺陷" if not open_defects else f"{len(open_defects)} 个未闭环缺陷"
    return {"latest_run": run["dir"], "open": open_defects, "closed": closed, "note": note}


@app.get("/api/cases")
def api_cases():
    return {"total": len(_scan_cases()), "cases": _scan_cases()}


@app.get("/api/runs")
def api_runs(limit: int = 10):
    runs = [_run_view(r) for r in _iter_runs()[:limit]]
    return {"count": len(runs), "runs": runs}


@app.post("/api/runs")
def api_run_trigger(payload: dict | None = None):
    """触发后台运行：body {group?: 分组名}；全量运行不传 group。"""
    body = payload or {}
    group = body.get("group") or None
    if group and group not in GROUP_FILE:
        return JSONResponse({"started": False, "error": f"未知分组: {group}"}, status_code=400)
    try:
        return _start_test_run(group)
    except RuntimeError as e:
        return JSONResponse({"started": False, "error": str(e)}, status_code=409)
    except (ValueError, OSError) as e:
        return JSONResponse({"started": False, "error": str(e)}, status_code=400)


@app.get("/api/runs/status")
def api_run_status():
    """后台运行进度；结束后返回最新 run。"""
    global _ACTIVE_RUN
    active = _ACTIVE_RUN
    latest = None
    if active is not None:
        proc = active["proc"]
        if proc.poll() is None:
            return {
                "running": True,
                "started_at": active["started_at"],
                "selector": active["selector"],
                "pid": active["pid"],
                "latest_run": latest,
            }
        _ACTIVE_RUN = None
    runs = _iter_runs()
    if runs:
        latest = _run_view(runs[0])
    return {"running": False, "latest_run": latest}


@app.get("/api/runs/{run_id}")
def api_run_detail(run_id: str):
    """单次运行明细（含每用例 status/detail/duration）。"""
    rj = REPORTS / run_id / "result.json"
    if not rj.is_file():
        return JSONResponse({"error": f"未找到运行记录: {run_id}"}, status_code=404)
    try:
        data = json.loads(rj.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return JSONResponse({"error": f"运行记录损坏: {run_id}"}, status_code=500)
    summary = data.get("summary", {})
    return {
        "run_id": run_id,
        "started_at": data.get("started_at", ""),
        "profile": (data.get("profile") or {}).get("name", ""),
        "cycle_hz": data.get("cycle_hz", 0),
        "passed": summary.get("passed", 0),
        "failed": summary.get("failed", 0),
        "total": summary.get("total", 0),
        "cases": data.get("cases", []),
    }


def _defect_manual(bug_id: str, action: str, reason: str | None) -> dict:
    """人工关闭/重开缺陷。以用例名为稳定 key（BUG 序号随运行轮次漂移）。"""
    runs = _iter_runs()
    view = _defects_view(runs[0] if runs else None)
    hit = next((x for x in view["open"] + view["closed"] if x["id"] == bug_id), None)
    if hit is None:
        return {"ok": False, "error": f"未找到缺陷: {bug_id}"}
    state = _load_defect_state()
    state[hit["case"]] = {"action": action, "reason": reason or "人工操作",
                          "ts": time.strftime("%Y-%m-%dT%H:%M:%S")}
    _save_defect_state(state)
    view = _defects_view(runs[0] if runs else None)
    return {"ok": True, "bug_id": bug_id, "case": hit["case"], "action": action,
            "defects": view}


@app.post("/api/defects/{bug_id}/close")
def api_defect_close(bug_id: str, payload: dict | None = None):
    return _defect_manual(bug_id, "CLOSE", (payload or {}).get("reason"))


@app.post("/api/defects/{bug_id}/reopen")
def api_defect_reopen(bug_id: str, payload: dict | None = None):
    return _defect_manual(bug_id, "REOPEN", (payload or {}).get("reason"))


@app.get("/api/defects")
def api_defects():
    runs = _iter_runs()
    return _defects_view(runs[0] if runs else None)


@app.get("/api/summary")
def api_summary():
    """控制台一次拉全：profile / 统计 / 用例 / 最近运行 / 缺陷。"""
    runs = _iter_runs()
    latest = _run_view(runs[0]) if runs else None
    cases = _scan_cases()
    by_group: dict[str, int] = {}
    for c in cases:
        by_group[c["group"]] = by_group.get(c["group"], 0) + 1
    return {
        "profile": (runs[0]["data"].get("profile") if runs else None) or {},
        "cycle_hz": latest["cycle_hz"] if latest else 0,
        "latest_run": latest,
        "runs": [_run_view(r) for r in runs[:10]],
        "stats": {
            "total_cases": len(cases),
            "by_group": by_group,
            "last_passed": latest["passed"] if latest else 0,
            "last_failed": latest["failed"] if latest else 0,
            "last_total": latest["total"] if latest else 0,
        },
        "defects": _defects_view(runs[0] if runs else None),
    }


if FRONTEND.is_dir():
    # 同源托管前端（http://127.0.0.1:58090 直接打开控制台）
    app.mount("/", StaticFiles(directory=str(FRONTEND), html=True), name="console-ui")
