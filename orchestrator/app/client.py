"""gRPC 客户端封装。

职责边界：Python 只负责【下发一次命令 + 接收批量轨迹 + 结果分析】；
控制周期内的高频采样全部在 C++ Agent 内部完成。
"""
from __future__ import annotations

from dataclasses import dataclass
import pathlib
import subprocess
import sys
import time

import grpc
import numpy as np

try:
    from generated import controller_pb2 as pb
    from generated import controller_pb2_grpc as pb_grpc
except ImportError:  # pragma: no cover
    _here = pathlib.Path(__file__).resolve().parent
    sys.path.insert(0, str(_here))
    sys.path.insert(0, str(_here.parent / "generated"))
    import controller_pb2 as pb  # type: ignore
    import controller_pb2_grpc as pb_grpc  # type: ignore


# 错误码与 proto 对齐
ERR_OK = 0
ERR_NOT_ENABLED = 1
ERR_JOINT_LIMIT = 2
ERR_BUSY = 3
ERR_ABORTED = 4
ERR_EMERGENCY_STOP = 5
ERR_FAULT = 6
ERR_UNKNOWN = 7

MOTION_IDLE = "IDLE"
MOTION_BUSY = "BUSY"
MOTION_ACTIVE = "ACTIVE"
MOTION_DONE = "DONE"
MOTION_ABORTED = "ABORTED"
MOTION_ERROR = "ERROR"


@dataclass
class Trajectory:
    """把 gRPC 批量轨迹转成 numpy 数组，供 Oracle 判定。"""

    t_ns: np.ndarray            # (n,) 采样时间戳 ns
    q: np.ndarray               # (dof, n) 位置 deg
    dq: np.ndarray              # (dof, n) 速度 deg/s
    ddq: np.ndarray             # (dof, n) 加速度 deg/s^2
    motion_state: list[str]     # (n,) 每采样点的运动状态

    @classmethod
    def from_result(cls, result) -> "Trajectory":
        samples = list(result.samples)
        n = len(samples)
        dof = len(samples[0].joints) if n else 0
        t = np.zeros(n, dtype=np.int64)
        q = np.zeros((dof, n))
        dq = np.zeros((dof, n))
        ddq = np.zeros((dof, n))
        states: list[str] = []
        for i, s in enumerate(samples):
            t[i] = s.timestamp_ns
            states.append(s.motion_state)
            for a in range(dof):
                q[a, i] = s.joints[a].position
                dq[a, i] = s.joints[a].velocity
                ddq[a, i] = s.joints[a].acceleration
        return cls(t_ns=t, q=q, dq=dq, ddq=ddq, motion_state=states)


class ControllerClient:
    """控制器指令客户端（PLCopen 语义）。"""

    def __init__(self, target: str = "127.0.0.1:50051"):
        self.target = target
        self._channel = grpc.insecure_channel(target)
        self.stub = pb_grpc.ControllerServiceStub(self._channel)

    # -- PLCopen 指令 ------------------------------------------------------
    def enable(self, timeout: float = 15.0):
        return self.stub.Enable(pb.EnableRequest(), timeout=timeout)

    def disable(self, timeout: float = 15.0):
        return self.stub.Disable(pb.DisableRequest(), timeout=timeout)

    def home(self, velocity: float = 60.0, acceleration: float = 150.0,
             deceleration: float = 150.0, timeout: float = 15.0):
        return self.stub.Home(
            pb.HomeRequest(velocity=velocity, acceleration=acceleration,
                           deceleration=deceleration), timeout=timeout
        )

    def move_absolute(self, target_position: list[float], velocity: float = 60.0,
                      acceleration: float = 150.0, deceleration: float = 150.0,
                      jerk: float = 1000.0, abort_current: bool = False,
                      acc: float | None = None, dec: float | None = None):
        if acc is not None:
            acceleration = acc
        if dec is not None:
            deceleration = dec
        return self.stub.MoveAbsolute(
            pb.MoveAbsoluteRequest(target_position=target_position,
                                   velocity=velocity,
                                   acceleration=acceleration,
                                   deceleration=deceleration,
                                   jerk=jerk,
                                   abort_current=abort_current)
        )

    def move_relative(self, delta: list[float], velocity: float = 60.0,
                      acceleration: float = 150.0, deceleration: float = 150.0,
                      jerk: float = 1000.0, timeout: float = 15.0):
        return self.stub.MoveRelative(
            pb.MoveRelativeRequest(delta=delta, velocity=velocity,
                                   acceleration=acceleration,
                                   deceleration=deceleration, jerk=jerk), timeout=timeout
        )

    def stop(self, emergency: bool = False, timeout: float = 15.0):
        return self.stub.Stop(pb.StopRequest(emergency=emergency), timeout=timeout)

    def reset(self, timeout: float = 15.0):
        return self.stub.Reset(pb.ResetRequest(), timeout=timeout)

    def get_state(self, timeout: float = 15.0):
        return self.stub.GetState(pb.GetStateRequest(), timeout=timeout)

    # -- 工具 --------------------------------------------------------------
    def wait_ready(self, timeout_s: float = 15.0) -> bool:
        self._last_ready_error = ""
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            try:
                self.get_state()
                return True
            except grpc.RpcError as e:
                self._last_ready_error = f"{e.code().name}: {e.details()}"
                time.sleep(0.2)
        return False

    def close(self):
        self._channel.close()


def spawn_agent(profile_path: str, port: int = 50051,
                exe: str | None = None,
                log_file: str | None = None) -> subprocess.Popen:
    """拉起 C++ Controller Agent 子进程（供 pytest 使用）。

    exe 解析顺序：显式参数 > 环境变量 RTP_AGENT_BIN > 常见构建输出路径。
    log_file 提供时 stdout/stderr 写入文件，避免 PIPE 缓冲填满阻塞 agent 线程。
    """
    repo = pathlib.Path(__file__).resolve().parents[2]
    candidates: list[str] = []
    if exe:
        candidates.append(exe)
    env_exe = os_environ("RTP_AGENT_BIN")
    if env_exe:
        candidates.append(env_exe)
    candidates += [
        # Unix Makefiles / Ninja 单配置生成器（本机 clang+mingw64、Linux CI）
        str(repo / "build" / "controller-agent" / "controller_agent.exe"),
        str(repo / "build" / "controller-agent" / "controller_agent"),
        # MSVC 多配置生成器
        str(repo / "build" / "controller-agent" / "Release" / "controller_agent.exe"),
        str(repo / "build" / "controller-agent" / "Debug" / "controller_agent.exe"),
    ]
    exe_path = next((c for c in candidates if pathlib.Path(c).exists()), None)
    if exe_path is None:
        raise FileNotFoundError(
            "未找到 controller_agent（.exe），请先构建 C++ 侧，或设置 RTP_AGENT_BIN="
            + " | ".join(candidates)
        )
    log_stream = open(log_file, "wb") if log_file else subprocess.PIPE
    return subprocess.Popen(
        [exe_path, "--profile", profile_path, "--port", str(port)],
        stdout=log_stream, stderr=subprocess.STDOUT,
    )


def os_environ(key: str) -> str:
    import os
    return os.environ.get(key, "")
