"""pytest 夹具：自动拉起 C++ Controller Agent、等待就绪、结束时生成报告。

链路：pytest -> gRPC -> C++ Agent -> Simulator -> 判定 -> 报告
"""
from __future__ import annotations

import pathlib
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parents[2]
ORCH = REPO / "orchestrator"
sys.path.insert(0, str(ORCH))

from app.client import ControllerClient, spawn_agent  # noqa: E402
from app.profile import RobotProfile  # noqa: E402
from app.report import RunContext, next_run_dir, write_run  # noqa: E402
from tests import capture  # noqa: E402

PORT = 50051

_context: RunContext | None = None
_run_dir: pathlib.Path | None = None
_agent_proc = None


@pytest.fixture(scope="session")
def profile() -> RobotProfile:
    return RobotProfile.load("maira_sim")


@pytest.fixture(scope="session")
def client(profile) -> ControllerClient:
    """启动 C++ Agent 子进程并等待 gRPC 就绪；会话结束自动清理。"""
    global _agent_proc
    proc = spawn_agent(str(profile.source_path), port=PORT)
    _agent_proc = proc
    c = ControllerClient(f"127.0.0.1:{PORT}")
    if not c.wait_ready(timeout_s=20):
        out = ""
        if proc.stdout:
            try:
                out = proc.stdout.read(4000)
            except Exception:
                out = ""
        pytest.fail(f"controller_agent 未就绪，输出: {out}")
    yield c
    c.close()
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()


@pytest.fixture(scope="session", autouse=True)
def _run_context(profile):
    """会话级运行上下文：记录用例结果，结束时写报告。"""
    global _context, _run_dir
    _context = RunContext(
        profile_name=profile.name,
        profile_model=profile.model,
        controller="Simulation",
        cycle_hz=profile.cycle_hz,
        port=PORT,
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
