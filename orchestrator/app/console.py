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
import csv
import json
import os
import pathlib
import subprocess
import sys
import threading
import time

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

REPO = pathlib.Path(__file__).resolve().parents[2]
TESTS = REPO / "orchestrator" / "tests"
ORCHESTRATOR = REPO / "orchestrator"
REPORTS = REPO / "reports"
FRONTEND = REPO / "frontend"
WAVEFORMS = REPORTS / "waveforms"
LIVE_ACTIVE = REPORTS / "active.live.jsonl"  # 运行中逐用例轨迹段（SSE 实时曲线数据源）
MANUAL_ACTIVE = REPORTS / "manual.live.active"  # 手动示波器活跃标记（示教器 jog 采样期间存在）
# 手动活跃新鲜度阈值：标记文件超过该秒数未更新即视为手动监控已停止（进程退出/残留防泄漏）
MANUAL_FRESH_SEC = float(os.environ.get("RTP_MANUAL_FRESH_SEC", "10"))

# 跨进程互斥锁：记录当前运行的 pid/启动时间，防止多实例/重复触发各起一个 pytest
RUN_LOCK_NAME = ".run.lock"
# 卡死（stalled）判定阈值：实时流超过该秒数没有任何新数据 → 判定卡死并自动中止
STALL_TIMEOUT_SEC = float(os.environ.get("RTP_STALL_TIMEOUT_SEC", "180"))


def _run_lock_file() -> pathlib.Path:
    """锁文件路径（跟随 REPORTS，测试 monkeypatch REPORTS 后自动指向临时目录）。"""
    return REPORTS / RUN_LOCK_NAME


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
# 触发/中止/状态查询的互斥锁（FastAPI 同步端点在线程池执行，存在并发窗口）
_RUN_LOCK = threading.Lock()


class _FinishedProc:
    """替身进程对象：立即处于"已完成"状态（测试用 fake runner 可复用）。"""

    def __init__(self, returncode: int = 0):
        self._rc = returncode

    def poll(self):
        return self._rc


def _subprocess_env() -> dict:
    """子进程环境：RTP_WRITE_REPORT=1 + Windows 下补齐 agent 运行时 DLL 路径。

    controller_agent.exe 依赖 msys64 的 gRPC/absl DLL；uvicorn 若由桌面
    启动（PATH 无 msys64），子进程 pytest 自动拉起 agent 会失败。
    RTP_MSYS_ROOT 可覆盖默认路径。
    """
    env = dict(os.environ)
    env["RTP_WRITE_REPORT"] = "1"
    msys = os.environ.get("RTP_MSYS_ROOT", r"C:\Users\jh\msys64")
    if sys.platform == "win32" and os.path.isdir(msys):
        extra = [msys + r"\mingw64\bin", msys + r"\clang64\bin", msys + r"\usr\bin"]
        env["PATH"] = os.pathsep.join(extra + [env.get("PATH", "")])
    return env


def _pid_alive(pid: int) -> bool:
    """进程是否存活（不引入 psutil 依赖）。"""
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    if sys.platform == "win32":
        try:
            # 不能用 text=True：tasklist 中文系统输出 GBK，解码抛异常会让 .stdout 变 None
            cp = subprocess.run(["tasklist", "/FI", f"PID eq {pid}"],
                                capture_output=True, timeout=5)
        except (OSError, subprocess.SubprocessError):
            return False
        out = (cp.stdout or b"").decode("utf-8", "replace")
        return str(pid) in out
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _read_run_lock() -> dict | None:
    """读取跨进程运行锁；文件缺失/损坏/对应进程已死 → None（视为无锁）。"""
    f = _run_lock_file()
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    if not _pid_alive(data.get("pid")):
        return None
    return data


def _write_run_lock(active: dict) -> None:
    """写入跨进程运行锁（pid + 启动时间 + 选择器）。"""
    f = _run_lock_file()
    try:
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps({
            "pid": active.get("pid"),
            "started_at": active.get("started_at"),
            "selector": active.get("selector"),
        }, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass


def _clear_run_lock() -> None:
    """释放锁：写回 pid=0（而不是删文件），避免清理路径依赖文件删除权限/钩子。"""
    try:
        f = _run_lock_file()
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps({"pid": 0, "released_at":
                                 time.strftime("%Y-%m-%dT%H:%M:%S")},
                                ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass


def _progress_signature() -> tuple:
    """实时流心跳指纹（文件大小 + 修改时间）；文件不存在 → (-1, -1)。"""
    try:
        st = LIVE_ACTIVE.stat()
    except OSError:
        return (-1, -1)
    return (st.st_size, int(st.st_mtime))


def _manual_active() -> bool:
    """手动示波器是否活跃：示教器 jog 采样期间 touch 标记文件，超时即失效。

    用新鲜度（mtime）而非文件存在性判定：teach_pendant 进程异常退出时
    标记文件残留，10s 后自动视为停止，SSE 不会因此永不收尾。
    """
    try:
        st = MANUAL_ACTIVE.stat()
    except OSError:
        return False
    return (time.time() - st.st_mtime) < MANUAL_FRESH_SEC


def _terminate_proc(proc, grace: float = 5.0) -> str:
    """终止运行进程；Windows 下必须杀整棵进程树（pytest 会拉起 controller agent）。

    返回实际使用的方式：taskkill / terminate / kill / failed。
    """
    pid = getattr(proc, "pid", None)
    if sys.platform == "win32":
        if pid:
            try:
                subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                               capture_output=True, timeout=15)
            except (OSError, subprocess.SubprocessError):
                pass
            return "taskkill"
        try:
            proc.terminate()
            return "terminate"
        except Exception:  # noqa: BLE001
            return "failed"
    try:
        proc.terminate()
    except Exception:  # noqa: BLE001
        pass
    deadline = time.time() + grace
    while time.time() < deadline:
        if proc.poll() is not None:
            return "terminate"
        time.sleep(0.2)
    try:
        proc.kill()
        return "kill"
    except Exception:  # noqa: BLE001
        return "failed"


def _finish_active() -> None:
    """清理运行态：置空 _ACTIVE_RUN、删锁、删实时流文件（SSE 据此收尾）。"""
    global _ACTIVE_RUN
    _ACTIVE_RUN = None
    _clear_run_lock()
    try:
        LIVE_ACTIVE.unlink()
    except BaseException:  # noqa: BLE001
        # 清理路径必须尽最大努力且不外溢：删除失败（占用/只读/宿主注入钩子）
        # 都不应让 status/cancel 接口变成 500，实时流文件本身运行结束即失效。
        pass


def _write_aborted_run(active: dict, status: str, reason: str) -> str | None:
    """为被中止/卡死的运行补一条运行记录，使其进入运行历史并派生缺陷。

    summary 记为 0 passed / 1 failed / 1 total → _run_view 判为 FAIL，
    「卡死」不会静默消失，而是像真实失败一样可追溯。
    """
    try:
        REPORTS.mkdir(parents=True, exist_ok=True)
        today = time.strftime("%Y%m%d")
        seq = 1
        while (REPORTS / f"run_{today}_{seq:03d}").exists():
            seq += 1
        d = REPORTS / f"run_{today}_{seq:03d}"
        d.mkdir(parents=True, exist_ok=True)
        data = {
            "run_id": d.name,
            "started_at": active.get("started_at", ""),
            "profile": {"name": active.get("selector", "")},
            "cycle_hz": 0,
            "aborted": True,
            "abort_reason": reason,
            "summary": {"passed": 0, "failed": 1, "total": 1},
            "cases": [{
                "name": f"__run_{status.lower()}__",
                "status": "FAIL",
                "duration": 0.0,
                "detail": reason,
            }],
        }
        (d / "result.json").write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return d.name
    except OSError:
        return None


def _start_test_run(group: str | None = None) -> dict:
    """启动后台 pytest 子进程（全量或单分组文件）。

    生产实现：真实 subprocess；测试通过 monkeypatch 本函数替换。
    """
    global _ACTIVE_RUN
    # 并发互斥：FastAPI 同步端点跑在线程池，两次 POST 之间存在检查-启动竞态窗口，
    # 必须加锁；同时先回收已自然结束的运行，再挡住新触发。
    with _RUN_LOCK:
        if _ACTIVE_RUN is not None and _ACTIVE_RUN["proc"].poll() is not None:
            _finish_active()
        if _ACTIVE_RUN is not None:
            raise RuntimeError(
                f"已有运行在进行中（pid={_ACTIVE_RUN.get('pid')}，"
                f"启动于 {_ACTIVE_RUN.get('started_at')}，"
                f"选择器 {_ACTIVE_RUN.get('selector')}）；"
                f"如需中止请先调用 POST /api/runs/cancel")
        # 跨进程残留：本实例没在运行，但锁文件里的 pid 仍活着（另一个控制台实例/孤儿 pytest）
        stray = _read_run_lock()
        if stray:
            raise RuntimeError(
                f"检测到未受本实例管理的运行中任务（pid={stray.get('pid')}，"
                f"启动于 {stray.get('started_at')}）；两个 pytest 同时驱动同一控制器会死锁。"
                f"请先结束该进程或调用 POST /api/runs/cancel")
        cmd = [sys.executable, "-m", "pytest", "-q", "-p", "no:warnings"]
        if group:
            f = GROUP_FILE.get(group)
            if f is None:
                raise ValueError(f"未知分组: {group}")
            cmd.append("tests/" + f)
        log = REPORTS / "manual_run.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        with log.open("wb") as lf:
            proc = subprocess.Popen(cmd, cwd=str(ORCHESTRATOR), env=_subprocess_env(),
                                    stdout=lf, stderr=subprocess.STDOUT)
        now = time.time()
        _ACTIVE_RUN = {
            "proc": proc,
            "pid": proc.pid,
            "started_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "selector": group or "全量",
            # 心跳：实时流指纹 + 最近一次有进展的时刻（卡死检测用）
            "last_sig": _progress_signature(),
            "last_progress": now,
        }
        _write_run_lock(_ACTIVE_RUN)
        return {"started": True, **{k: v for k, v in _ACTIVE_RUN.items()
                                    if k not in ("proc", "last_sig", "last_progress")},
                "proc": None, "stall_timeout_sec": STALL_TIMEOUT_SEC}


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


def _active_stream_gen(live: pathlib.Path, is_active) -> str:
    """SSE 生成器：推送 live 文件新增行；文件消失且无运行 → 结束。

    - 运行中：每 400ms 读尾部新行，增量推送（data: <json>\n\n）；
    - 结束判定：连续无新数据且 is_active() 为 False → 断开（前端 EventSource 关闭）。
    """
    pos = 0
    idle = 0
    while True:
        if live.is_file():
            try:
                with live.open(encoding="utf-8") as f:
                    f.seek(pos)
                    new = f.read()
                    pos = f.tell()
            except OSError:
                break
            if new:
                idle = 0
                for line in new.splitlines():
                    if line.strip():
                        yield "data: " + line + "\n\n"
                continue
        idle += 1
        if not is_active() and idle > 1:
            break
        time.sleep(0.4)


@app.get("/api/runs/active/stream")
def api_active_stream():
    """SSE 实时曲线：运行过程中逐用例轨迹段边跑边推（示波器语义）。

    前端 EventSource 连接；运行结束（live 文件被清理）→ 流自动结束。
    数据全部来自 C++ Agent 批量采样后逐用例落盘的 active.live.jsonl。
    手动示波器（示教器 jog 采样）期间同样保持活跃（见 /api/manual/active）。
    """
    return StreamingResponse(
        _active_stream_gen(LIVE_ACTIVE,
                           lambda: _ACTIVE_RUN is not None or _manual_active()),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/manual/active")
def api_manual_active():
    """手动示波器活跃状态：示教器 jog 采样期间 true（前端 2s 心跳探测后连 SSE）。"""
    return {"active": _manual_active()}


@app.get("/api/runs/status")
def api_run_status():
    """后台运行进度 + 心跳/卡死检测；结束后返回最新 run。

    心跳语义：以实时流文件 active.live.jsonl 的大小/修改时间作为"进展指纹"，
    指纹变化即刷新 last_progress。超过 STALL_TIMEOUT_SEC（默认 180s，
    可用 RTP_STALL_TIMEOUT_SEC 覆盖）仍无进展 → 判定卡死：
    自动杀掉进程树，并补一条 FAILED 运行记录（不静默消失，可派生缺陷）。
    """
    global _ACTIVE_RUN
    with _RUN_LOCK:
        active = _ACTIVE_RUN
        if active is not None and active["proc"].poll() is not None:
            _finish_active()                     # 自然结束：清理并落到下面返回 latest
            active = None
        if active is not None:
            sig = _progress_signature()
            now = time.time()
            prev = active.get("last_sig")
            if prev is None or tuple(prev) != sig:
                active["last_sig"] = sig
                active["last_progress"] = now
            idle = now - float(active.get("last_progress", now))
            if idle > STALL_TIMEOUT_SEC:
                # 卡死：终止 + 落失败记录 + 清理
                how = _terminate_proc(active["proc"])
                reason = (f"运行卡死：{int(idle)}s 内实时流无新数据"
                          f"（阈值 {int(STALL_TIMEOUT_SEC)}s），已自动中止")
                run_id = _write_aborted_run(active, "STALLED", reason)
                selector, pid, started_at = (active.get("selector"),
                                             active.get("pid"), active.get("started_at"))
                _finish_active()
                runs = _iter_runs()
                latest = _run_view(runs[0]) if runs else None
                return {
                    "running": False,
                    "stalled": True,
                    "auto_cancelled": True,
                    "stalled_seconds": round(idle, 1),
                    "stall_timeout_sec": STALL_TIMEOUT_SEC,
                    "method": how,
                    "aborted_run": run_id,
                    "selector": selector,
                    "pid": pid,
                    "started_at": started_at,
                    "latest_run": latest,
                }
            return {
                "running": True,
                "started_at": active.get("started_at"),
                "selector": active.get("selector"),
                "pid": active.get("pid"),
                "latest_run": None,
                "stalled": False,
                "idle_seconds": round(idle, 1),
                "stall_timeout_sec": STALL_TIMEOUT_SEC,
            }
    runs = _iter_runs()
    latest = _run_view(runs[0]) if runs else None
    return {"running": False, "stalled": False,
            "stall_timeout_sec": STALL_TIMEOUT_SEC, "latest_run": latest}


@app.post("/api/runs/cancel")
def api_run_cancel(payload: dict | None = None):
    """中止当前运行：先杀进程树，再补一条 ABORTED 运行记录。

    body 可选 {"reason": "..."}。用于运行卡死/误触发时人工介入，
    不必再手动 taskkill。无运行中任务 → 409。
    """
    global _ACTIVE_RUN
    body = payload or {}
    reason = body.get("reason") or "用户手动中止"
    with _RUN_LOCK:
        active = _ACTIVE_RUN
        if active is None:
            return JSONResponse({"cancelled": False, "error": "当前没有运行中的任务"},
                                status_code=409)
        proc = active["proc"]
        if proc.poll() is not None:
            _finish_active()
            runs = _iter_runs()
            latest = _run_view(runs[0]) if runs else None
            return {"cancelled": False, "error": "运行已结束（无需中止）", "latest_run": latest}
        how = _terminate_proc(proc)
        run_id = _write_aborted_run(active, "ABORTED", reason)
        info = {"cancelled": True, "method": how, "aborted_run": run_id,
                "pid": active.get("pid"), "selector": active.get("selector"),
                "started_at": active.get("started_at"), "reason": reason}
        _finish_active()
        return info


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
        "has_trajectory": (REPORTS / run_id / "trajectory.csv").is_file(),
        "has_waveforms": _waveform_names() is not None,
        "cases": data.get("cases", []),
    }


@app.get("/api/runs/{run_id}/waveform")
def api_run_waveform(run_id: str):
    """运行的数据曲线（逐周期 jitter/latency 序列）。

    数据源：test_waveform 产生的 run_*/waveform.json；仅全量或"波形报告"
    分组运行才会产出。返回 {"curves": [...]}；无波形数据时 curves 为空。
    """
    run_dir = REPORTS / run_id
    # 运行本身不存在 → 404（与 /api/runs/{run_id} 一致）
    if not (run_dir / "result.json").is_file():
        return JSONResponse({"error": f"未找到运行记录: {run_id}"}, status_code=404)
    wj = run_dir / "waveform.json"
    if not wj.is_file():
        return {"run_id": run_id, "curves": []}
    try:
        data = json.loads(wj.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return JSONResponse({"error": f"波形数据损坏: {run_id}"}, status_code=500)
    if not isinstance(data, list):
        return JSONResponse({"error": f"波形数据格式异常: {run_id}"}, status_code=500)
    return {"run_id": run_id, "curves": data}


def _waveform_names():
    """reports/waveforms 下可用 SVG 波形名列表（空/不存在 → None）。"""
    if not WAVEFORMS.is_dir():
        return None
    names = sorted(f.name for f in WAVEFORMS.glob("*.svg"))
    return names or None


def _sample_trajectory(run_id: str, max_points: int = 500):
    """读取运行轨迹采样（trajectory.csv）并等间隔抽稀。

    返回 {time_ms, q[7], dq[7], ddq[7]}；不足抽稀直接全量。
    """
    csvf = REPORTS / run_id / "trajectory.csv"
    if not csvf.is_file():
        return None
    t0 = None
    ts: list[float] = []
    cols = {"q": [[] for _ in range(7)], "dq": [[] for _ in range(7)], "ddq": [[] for _ in range(7)]}
    try:
        with csvf.open(encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                try:
                    ns = int(row["timestamp_ns"])
                except (ValueError, KeyError):
                    continue
                if t0 is None:
                    t0 = ns
                ts.append((ns - t0) / 1e6)  # ms
                for k, names in (("q", [f"q{i}" for i in range(7)]),
                                 ("dq", [f"dq{i}" for i in range(7)]),
                                 ("ddq", [f"ddq{i}" for i in range(7)])):
                    for i in range(7):
                        try:
                            cols[k][i].append(float(row[names[i]]))
                        except (ValueError, KeyError):
                            cols[k][i].append(0.0)
    except (OSError, ValueError):
        return None
    n = len(ts)
    if n == 0:
        return None
    if n > max_points:
        idx = [round(i * (n - 1) / (max_points - 1)) for i in range(max_points)]
        keep = lambda a: [a[i] for i in idx]
        ts = keep(ts)
        cols = {k: [keep(a) for a in arr] for k, arr in cols.items()}
    return {"sample_count": n, "max_points": len(ts), "time_ms": ts,
            "q": cols["q"], "dq": cols["dq"], "ddq": cols["ddq"]}


@app.get("/api/runs/{run_id}/trajectory")
def api_run_trajectory(run_id: str, max_points: int = 500):
    """运行轨迹曲线数据（抽稀后），供控制台画位置/速度/加速度曲线。"""
    data = _sample_trajectory(run_id, min(max(max_points, 5), 2000))
    if data is None:
        return JSONResponse({"error": f"该运行无轨迹采样: {run_id}"}, status_code=404)
    return {"run_id": run_id, **data}


@app.get("/api/waveforms/{name}")
def api_waveform(name: str):
    """返回 reports/waveforms/*.svg（白名单防路径穿越）。"""
    if not name.endswith(".svg") or "/" in name or "\\" in name or name.startswith("."):
        return JSONResponse({"error": "非法文件名"}, status_code=400)
    f = WAVEFORMS / name
    if not f.is_file():
        return JSONResponse({"error": f"未找到波形: {name}"}, status_code=404)
    return FileResponse(str(f), media_type="image/svg+xml")


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
