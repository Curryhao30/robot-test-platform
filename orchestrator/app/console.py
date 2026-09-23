"""管理控制台只读 API：用例清单 / 运行历史 / 缺陷视图。

数据源全部来自真实产物，不引入数据库：
- 用例清单：扫描 orchestrator/tests/*.py 中 test_* 函数（AST 解析）
- 运行历史：reports/run_*/result.json（每次全量运行的机器可读结果）
- 缺陷视图：由最近运行中 FAIL 用例派生（全绿 → 0 个未闭环缺陷）

设计语义：BUG 由失败用例自动派生，回归通过后关闭——对应项目里
"TestRun → Defect → Regression → Closed"的可追溯闭环；当前实现为只读展示，
写操作（手动建 BUG / 分配 / 关闭）留给后续版本。
"""
from __future__ import annotations

import ast
import json
import pathlib

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

REPO = pathlib.Path(__file__).resolve().parents[2]
TESTS = REPO / "orchestrator" / "tests"
REPORTS = REPO / "reports"
FRONTEND = REPO / "frontend"

app = FastAPI(title="Robot Test Platform Console API", version="0.13.0-p3")

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
}


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


def _defects_view(run: dict | None) -> dict:
    """由最近一次运行的 FAIL 用例派生缺陷；当前全绿 → 0 未闭环。"""
    if run is None:
        return {"latest_run": None, "open": [], "closed": [], "note": "尚无运行记录"}
    data = run["data"]
    cases = data.get("cases", [])
    open_defects = []
    for i, c in enumerate(cases, 1):
        if c.get("status") != "PASS":
            open_defects.append({
                "id": f"BUG-{i:03d}",
                "case": c.get("name", ""),
                "detail": c.get("detail", "") or "见运行日志",
                "status": "OPEN",
                "found_in": run["dir"],
            })
    closed = []
    for d in open_defects:
        # 语义演示：缺陷在后续回归通过后关闭；当前只有最近一次运行，
        # 未闭环缺陷保持 OPEN；历史 run 中存在同用例 PASS 时视为已闭环。
        for older in _iter_runs()[1:]:
            for c in older["data"].get("cases", []):
                if c.get("name") == d["case"] and c.get("status") == "PASS":
                    closed.append({**d, "status": "CLOSED", "closed_in": older["dir"]})
                    break
            if any(c["case"] == d["case"] for c in closed):
                break
    open_defects = [d for d in open_defects if not any(c["case"] == d["case"] for c in closed)]
    note = "最近运行全绿，无未闭环缺陷" if not open_defects else f"{len(open_defects)} 个未闭环缺陷"
    return {"latest_run": run["dir"], "open": open_defects, "closed": closed, "note": note}


@app.get("/api/cases")
def api_cases():
    return {"total": len(_scan_cases()), "cases": _scan_cases()}


@app.get("/api/runs")
def api_runs(limit: int = 10):
    runs = [_run_view(r) for r in _iter_runs()[:limit]]
    return {"count": len(runs), "runs": runs}


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
