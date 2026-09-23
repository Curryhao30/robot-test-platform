"""pytest 夹具：自动拉起 C++ Controller Agent、等待就绪、结束时生成报告。

链路：pytest -> gRPC -> C++ Agent -> Simulator -> 判定 -> 报告
"""
from __future__ import annotations

import os
import pathlib
import socket
import subprocess
import sys
import time

import pytest

REPO = pathlib.Path(__file__).resolve().parents[2]
ORCH = REPO / "orchestrator"
sys.path.insert(0, str(ORCH))

from app.adapters import adapter_config, connect_hil, make_grpc_adapter  # noqa: E402
from app.client import ControllerClient, spawn_agent  # noqa: E402
from app.profile import RobotProfile  # noqa: E402
from app.report import RunContext, next_run_dir, write_run  # noqa: E402
from tests import capture  # noqa: E402

MAX_SPAWN_ATTEMPTS = 5

# 候选固定端口：避开常见占用区间；Windows Hyper-V 排除范围 / CI 偶发占用
# 都可能使单个端口绑定失败（gRPC AddListeningPort 返回 0 且不抛异常），
# 因此按序尝试，绑定失败（agent FATAL 立即退出）自动换下一个。
CANDIDATE_PORTS = [50051, 50551, 51051, 51551, 52051, 52551]


def _pick_candidate_port(attempt: int) -> int:
    return CANDIDATE_PORTS[attempt % len(CANDIDATE_PORTS)]


PORT = _pick_candidate_port(0)

_context: RunContext | None = None
_run_dir: pathlib.Path | None = None
_agent_proc = None
_agent_port: int = PORT


@pytest.fixture(scope="session")
def profile() -> RobotProfile:
    return RobotProfile.load("maira_sim")


def _parse_agent_port(log_path: str, timeout_s: float = 5.0) -> int | None:
    """从 agent 日志解析真实监听端口。

    读到 FATAL（绑定失败）立即返回 None；成功行形如 `[agent] PORT=<n>`。
    """
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            with open(log_path, encoding="utf-8", errors="replace") as f:
                content = f.read()
            if "FATAL" in content:
                return None
            for line in content.splitlines():
                if line.startswith("[agent] PORT="):
                    return int(line.split("=", 1)[1].strip())
        except FileNotFoundError:
            pass
        except (ValueError, OSError):
            pass
        time.sleep(0.1)
    return None


def _try_spawn(profile_path: pathlib.Path, log_path: str, attempt: int):
    """尝试一次：候选端口 -> 拉起 agent -> 解析端口 -> 等待就绪。

    返回 (proc, client, None) 成功；失败返回 (None, None, 原因)。
    绑定失败时 agent FATAL 立即退出（return 3），日志会写 FATAL 行。
    """
    global _agent_port
    port = _pick_candidate_port(attempt)
    log = log_path.replace(".log", f"_{attempt}.log")
    proc = spawn_agent(str(profile_path), port=port, log_file=log)
    reported = _parse_agent_port(log, timeout_s=5.0)
    if reported is None:
        alive = proc.poll() is None
        if alive:
            proc.kill()
        return None, None, f"agent 未报告端口或绑定失败（alive={alive}，attempt={attempt}，端口 {port}）"
    if reported != port:
        alive = proc.poll() is None
        if alive:
            proc.kill()
        return None, None, f"端口不一致 agent={reported} 期望={port}（attempt={attempt}）"
    c = ControllerClient(f"127.0.0.1:{port}")
    if c.wait_ready(timeout_s=15):
        _agent_port = port
        try:
            import shutil
            shutil.copy(log, log_path)
        except OSError:
            pass
        return proc, c, None
    err = getattr(c, "_last_ready_error", "")
    alive = proc.poll() is None
    c.close()
    proc.kill()
    return None, None, f"未就绪 alive={alive} err={err}（attempt={attempt}，端口 {port}）"


@pytest.fixture(scope="session")
def client(profile) -> "RobotAdapter":
    """启动被测对象（Simulation: 拉起 C++ Agent；HIL: 连接真机盒子）并返回
    RobotAdapter。用例层只见 adapter，不见 ControllerClient（P2.0）。"""
    global _agent_proc
    cfg = adapter_config(profile)
    if cfg["type"] == "hil":
        try:
            adp = connect_hil(profile)
        except RuntimeError as e:
            pytest.fail(f"HIL 连接失败: {e}")
        yield adp
        adp.close()
        return

    (REPO / "reports").mkdir(parents=True, exist_ok=True)
    log_path = str(REPO / "reports" / "agent.log")
    last_err = ""
    for attempt in range(MAX_SPAWN_ATTEMPTS):
        proc, c, err = _try_spawn(profile.source_path, log_path, attempt)
        if proc is not None:
            _agent_proc = proc
            yield make_grpc_adapter(c, profile)
            c.close()
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except Exception:
                    proc.kill()
            return
        last_err = err
    out = ""
    try:
        with open(log_path, encoding="utf-8", errors="replace") as f:
            out = f.read(4000)
    except Exception:
        out = ""
    pytest.fail(f"controller_agent 启动失败（{MAX_SPAWN_ATTEMPTS} 次重试后: {last_err}），日志 {log_path}: {out}")


@pytest.fixture(scope="session", autouse=True)
def _run_context(profile):
    """会话级运行上下文：记录用例结果，结束时写报告。"""
    global _context, _run_dir
    _context = RunContext(
        profile_name=profile.name,
        profile_model=profile.model,
        controller="Simulation",
        cycle_hz=profile.cycle_hz,
        port=_agent_port,
    )
    _run_dir = next_run_dir(REPO / "reports")
    yield _context
    if _run_dir is not None:
        write_run(_context, _run_dir, trajectory=capture.TRAJECTORY)
        print(f"\n[report] {_run_dir}")


@pytest.hookimpl(tryfirst=True, hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    rep = outcome.get_result()
    if rep.when == "call" and _context is not None:
        name = item.name
        if rep.passed:
            status = "PASS"
        elif rep.failed:
            status = "FAIL"
        else:
            status = "ERROR"
        detail = capture.DETAILS.get(name, "")
        if not rep.passed and rep.longrepr:
            detail = (detail + "\n" + str(rep.longrepr).strip()).strip()
        _context.add(name, status, detail=detail,
                     duration_s=getattr(rep, "duration", 0.0))


# ---------------------------------------------------------------------------
# Virtual Teach Pendant：FastAPI 后端（uvicorn 子进程）+ Playwright 浏览器
# ---------------------------------------------------------------------------
TP_PORTS = [58081, 58082, 58083]


@pytest.fixture(scope="session")
def teach_url(client, profile) -> str:
    """启动示教器后端（uvicorn 子进程，复用 agent 的 gRPC 通道）。"""
    proc = None
    last_err = ""
    for port in TP_PORTS:
        env = dict(os.environ)
        env["RTP_AGENT_TARGET"] = "127.0.0.1:%d" % _agent_port
        env["RTP_PROFILE"] = str(profile.source_path)
        env["RTP_TP_PORT"] = str(port)
        log = open(str(REPO / "reports" / "teach_api.log"), "wb")
        proc = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "teach_pendant.main:app",
             "--host", "127.0.0.1", "--port", str(port)],
            cwd=str(ORCH), env=env, stdout=log, stderr=subprocess.STDOUT,
        )
        deadline = time.time() + 15
        ok = False
        while time.time() < deadline:
            if proc.poll() is not None:
                break
            try:
                import urllib.request
                with urllib.request.urlopen(
                        "http://127.0.0.1:%d/api/status" % port, timeout=1) as r:
                    if r.status == 200:
                        ok = True
                        break
            except Exception:
                time.sleep(0.2)
        if ok:
            yield "http://127.0.0.1:%d" % port
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except Exception:
                proc.kill()
            return
        last_err = "teach api 端口 %d 未就绪" % port
        proc.kill()
        proc.wait(timeout=3)
    log.close()
    pytest.fail("示教器后端启动失败: %s" % last_err)


@pytest.fixture(scope="session")
def pw_browser():
    """Playwright Chromium；本机无 chromium 时回退系统 Edge（Windows）。"""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as e:
        pytest.fail("未安装 playwright: %s" % e)
    with sync_playwright() as p:
        browser = None
        try:
            browser = p.chromium.launch()
        except Exception:
            try:
                browser = p.chromium.launch(channel="msedge")
            except Exception:
                pytest.fail("Playwright 无法启动 chromium 或 msedge")
        yield browser
        browser.close()
