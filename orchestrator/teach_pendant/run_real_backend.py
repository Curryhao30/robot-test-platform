"""真实模式启动器：拉起 C++ Controller Agent（gRPC）+ 示教器 REST 后端。

链路：
    controller_agent.exe --profile robot_profiles/maira_sim.yaml --port <AGENT_PORT>
        -> gRPC 127.0.0.1:<AGENT_PORT>
    uvicorn teach_pendant.main:app --port 58081
        -> REST 127.0.0.1:58081（/api/* 桥接到上面的 gRPC）
    PyQt5 面板：py qt_panel.py --url http://127.0.0.1:58081

用法：
    py teach_pendant/run_real_backend.py            # 默认 agent:50051, REST:58081
    py teach_pendant/run_real_backend.py --tp-port 58082
Ctrl+C 会同时终止 uvicorn 与本启动器拉起的 agent 子进程。
"""
from __future__ import annotations

import argparse
import os
import signal
import sys
import time


def _ensure_msys_path() -> None:
    """把 MSYS2/MinGW 运行时目录加进 PATH。

    C++ Controller Agent（MinGW 构建）运行时依赖 gRPC/protobuf/abseil 等 DLL，
    这些在 msys64 的 bin 下；pytest 在 MSYS2 终端跑通正是因为它在 PATH 上。
    独立启动（PowerShell/cmd）需手动补上，否则 agent 静默退出（ExitCode 空）。
    可用环境变量 RTP_MSYS_BIN 覆盖/追加。
    """
    cur = os.environ.get("PATH", "")
    cur_dirs = [p for p in cur.split(os.pathsep) if p]
    candidates = []
    env_extra = os.environ.get("RTP_MSYS_BIN", "")
    if env_extra:
        candidates += [p for p in env_extra.split(os.pathsep) if p]
    candidates += [
        r"C:\Users\jh\msys64\mingw64\bin",
        r"C:\Users\jh\msys64\clang64\bin",
        r"C:\Users\jh\msys64\usr\bin",
    ]
    added = [p for p in candidates if os.path.isdir(p) and p not in cur_dirs]
    if added:
        os.environ["PATH"] = os.pathsep.join(added + cur_dirs)


ORCH = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
TP = os.path.join(ORCH, "teach_pendant")
sys.path.insert(0, ORCH)
sys.path.insert(0, TP)

from app.client import spawn_agent, ControllerClient  # noqa: E402
from app.profile import RobotProfile  # noqa: E402
import uvicorn  # noqa: E402
import main as tp  # teach_pendant/main.py  # noqa: E402

CANDIDATE_AGENT_PORTS = [50051, 50551, 51051, 51551, 52051, 52551]


def main() -> int:
    ap = argparse.ArgumentParser(description="真实模式：C++ Agent + 示教器 REST 后端")
    ap.add_argument("--tp-port", type=int, default=58081, help="示教器 REST 端口")
    ap.add_argument("--agent-port", type=int, default=None,
                    help="C++ Agent gRPC 端口（默认自动从候选端口选可用者）")
    args = ap.parse_args()

    _ensure_msys_path()  # 让 C++ Agent 能找到 gRPC 等运行时 DLL

    profile = RobotProfile.load("maira_sim")
    log_dir = TP
    agent_proc = None
    agent_port = args.agent_port
    last_err = ""

    ports = [agent_port] if agent_port else CANDIDATE_AGENT_PORTS
    for port in ports:
        log_file = os.path.join(log_dir, f"agent_run_{port}.log")
        agent_proc = spawn_agent(str(profile.source_path), port=port,
                                 log_file=log_file)
        c = ControllerClient(f"127.0.0.1:{port}")
        if c.wait_ready(15):
            agent_port = port
            break
        last_err = getattr(c, "_last_ready_error", "unknown")
        try:
            c.close()
        except Exception:
            pass
        if agent_proc.poll() is None:
            agent_proc.kill()
        agent_proc = None
    else:
        print(f"[run_real_backend] C++ Agent 启动失败（候选端口均不可用）: {last_err}", flush=True)
        return 1

    print(f"[run_real_backend] C++ Agent 就绪 @ 127.0.0.1:{agent_port}", flush=True)
    os.environ["RTP_AGENT_TARGET"] = f"127.0.0.1:{agent_port}"
    os.environ["RTP_PROFILE"] = str(profile.source_path)
    os.environ["RTP_TP_PORT"] = str(args.tp_port)

    # 注入后端 gRPC 通道（lifespan 也会再注入一次，这里提前确保就绪）
    tp.start(os.environ["RTP_AGENT_TARGET"], os.environ["RTP_PROFILE"])
    print(f"[run_real_backend] 示教器 REST 后端 -> http://127.0.0.1:{args.tp_port}", flush=True)
    print("[run_real_backend] 另开终端运行："
          f"py teach_pendant/qt_panel.py --url http://127.0.0.1:{args.tp_port}", flush=True)

    def _shutdown(*_):
        print("[run_real_backend] 收到终止信号，关闭 agent 子进程…", flush=True)
        try:
            if agent_proc and agent_proc.poll() is None:
                agent_proc.terminate()
                agent_proc.wait(timeout=5)
        except Exception:
            pass
        os._exit(0)

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    try:
        uvicorn.run(tp.app, host="127.0.0.1", port=args.tp_port)
    finally:
        _shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
