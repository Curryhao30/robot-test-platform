"""虚拟示教器后端：REST 桥接到 Controller Agent（gRPC）。

前端（static/index.html）只通过 REST 交互；本模块复用
orchestrator/app/client.py 的 ControllerClient，与 pytest 走同一 gRPC 通道，
保证"UI 操作 -> Controller State"的联合测试语义。
"""
from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path

import yaml
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app.client import ControllerClient


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """启动时注入 Controller Agent 通道与 Profile（环境变量由测试入口设置）。"""
    target = os.environ.get("RTP_AGENT_TARGET")
    profile_path = os.environ.get("RTP_PROFILE")
    if target and profile_path:
        start(target, profile_path)
    yield


app = FastAPI(title="Virtual Teach Pendant API", lifespan=lifespan)

client: ControllerClient | None = None
profile: dict = {}

JOG_STEP_DEG = 5.0
DOF = 7


class ServoRequest(BaseModel):
    on: bool


class JogRequest(BaseModel):
    joint: int
    direction: int = 1        # +1 / -1
    speed_pct: float = 20.0   # 保留：P0 恒为增量步进


class MoveAbsRequest(BaseModel):
    target: list[float]


def _require() -> ControllerClient:
    if client is None:
        raise RuntimeError("teach pendant backend not started")
    return client


def _joints(st) -> list[float]:
    return [j.position for j in st.joints]


@app.get("/api/info")
def info():
    ctrl = profile.get("controller", {})
    return {
        "name": profile.get("name", "maira_sim"),
        "dof": DOF,
        "cycle_hz": ctrl.get("cycle_hz", 1000),
    }


@app.get("/api/status")
def status():
    c = _require()
    st = c.get_state()
    return {
        "enabled": st.enabled,
        "moving": st.moving,
        "error": st.error,
        "error_code": st.error_code,
        "error_message": st.error_message,
        "motion_state": st.motion_state,
        "joints": _joints(st),
    }


@app.post("/api/servo")
def servo(req: ServoRequest):
    c = _require()
    r = c.enable() if req.on else c.disable()
    return {"ok": r.ok, "error_code": r.error_code,
            "error_message": r.error_message, "motion_state": r.motion_state}


@app.post("/api/jog")
def jog(req: JogRequest):
    c = _require()
    if not (0 <= req.joint < DOF):
        return {"ok": False, "error_code": 7, "error_message": "joint out of range"}
    delta = [0.0] * DOF
    delta[req.joint] = JOG_STEP_DEG * (1 if req.direction >= 0 else -1)
    # 增量步进：先 Stop 清掉可能存在的运动，再执行一次小步；
    # UI 轮询看到位置变化。move_relative 为同步 RPC，返回时已到位。
    c.stop()
    r = c.move_relative(delta, velocity=30.0)
    return {"ok": r.ok, "error_code": r.error_code,
            "error_message": r.error_message, "motion_state": r.motion_state}


@app.post("/api/stop")
def stop():
    c = _require()
    r = c.stop()
    return {"ok": r.ok, "error_code": r.error_code,
            "error_message": r.error_message, "motion_state": r.motion_state}


@app.post("/api/reset")
def reset():
    c = _require()
    r = c.reset()
    return {"ok": r.ok, "error_code": r.error_code,
            "error_message": r.error_message, "motion_state": r.motion_state}


@app.post("/api/move_absolute")
def move_abs(req: MoveAbsRequest):
    c = _require()
    if len(req.target) != DOF:
        return {"ok": False, "error_code": 7,
                "error_message": f"target 需要 {DOF} 个关节，收到 {len(req.target)}"}
    r = c.move_absolute(req.target, velocity=30.0)
    return {"ok": r.ok, "error_code": r.error_code,
            "error_message": r.error_message, "motion_state": r.motion_state}


@app.get("/api/alarm")
def alarm():
    c = _require()
    st = c.get_state()
    alarms = []
    if st.error:
        alarms.append({"code": st.error_code,
                       "message": st.error_message or "unknown"})
    return {"error": st.error, "alarms": alarms}


def _load_profile(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def start(target: str, profile_path: str) -> None:
    """注入 Controller Agent 通道与 Profile（由测试/部署入口调用）。"""
    global client, profile
    client = ControllerClient(target)
    profile = _load_profile(profile_path)


def main() -> None:
    import uvicorn
    start(os.environ["RTP_AGENT_TARGET"], os.environ["RTP_PROFILE"])
    uvicorn.run(app, host="127.0.0.1", port=int(os.environ.get("RTP_TP_PORT", "8080")))


_static = Path(__file__).resolve().parent / "static"
app.mount("/", StaticFiles(directory=str(_static), html=True), name="static")


if __name__ == "__main__":
    main()
