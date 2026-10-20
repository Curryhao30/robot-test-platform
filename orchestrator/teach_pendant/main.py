"""虚拟示教器后端：REST 桥接到 Controller Agent（gRPC）。

前端（static/index.html）只通过 REST 交互；本模块复用
orchestrator/app/client.py 的 ControllerClient，与 pytest 走同一 gRPC 通道，
保证"UI 操作 -> Controller State"的联合测试语义。

手动示波器（P3 扩展）：第一次 jog 后启动常驻采样线程（MANUAL_HZ），
每个采样周期读控制器关节位置，按批写入 reports/active.live.jsonl
（与控制台 SSE 实时曲线同一数据源），并 touch reports/manual.live.active
标记手动活跃 —— 控制台前端据此自动连上实时曲线，"像示波器一样边动边画"。
"""
from __future__ import annotations

import json
import os
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

import yaml
from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app.client import ControllerClient

REPO = Path(__file__).resolve().parents[2]
REPORTS = REPO / "reports"
LIVE_ACTIVE = REPORTS / "active.live.jsonl"      # 与控制台 SSE 实时曲线同一文件
MANUAL_ACTIVE = REPORTS / "manual.live.active"   # 手动活跃标记（控制台 10s 新鲜度判定）

MANUAL_HZ = 50                # 采样频率（示波器观感，agent 1000Hz 控制周期内充足）
MANUAL_BATCH_MS = 240         # 每批点数对应毫秒数 → 每行一段（SSE 增量推送粒度）
MANUAL_MAX_BYTES = 4 * 1024 * 1024  # live 文件上限：超限清空轮转（防长挂爆盘）
MANUAL_FRESH_SEC = 10.0       # 手动活跃新鲜度（与 console 一致：标记过期视为已停止）
MANUAL_IDLE_SEC = 8.0         # 空闲自动停止：连续 N 秒无 jog 操作则停止采样（页面定格，不再无限增长）


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """启动时注入 Controller Agent 通道与 Profile（环境变量由测试入口设置）。"""
    target = os.environ.get("RTP_AGENT_TARGET")
    profile_path = os.environ.get("RTP_PROFILE")
    if target and profile_path:
        start(target, profile_path)
    # 干净启动：清掉上次进程残留的手动活跃标记（避免 SSE 判定为仍活跃）
    try:
        MANUAL_ACTIVE.unlink()
    except BaseException:  # noqa: BLE001
        pass
    yield
    _stop_manual_sampling()


app = FastAPI(title="Virtual Teach Pendant API", lifespan=lifespan)

client: ControllerClient | None = None
profile: dict = {}

# 手动采样线程状态（第一次 jog 激活；无操作 MANUAL_IDLE_SEC 秒后自动停止）
_sampler: threading.Thread | None = None
_sample_stop: threading.Event | None = None
_last_jog_ts: float = 0.0      # 最近一次 jog 操作时刻（采样线程据此判定空闲）

JOG_STEP_DEG = 5.0
DOF = 7


class ServoRequest(BaseModel):
    on: bool


class JogRequest(BaseModel):
    joint: int
    direction: int = 1        # +1 / -1
    speed_pct: float = 20.0   # 保留：P0 恒为增量步进
    step_deg: Optional[float] = None   # 桌面 HMI 倍率（0.1/1/5/10）；缺省用 JOG_STEP_DEG


class MoveAbsRequest(BaseModel):
    target: list[float]


def _require() -> ControllerClient:
    if client is None:
        raise RuntimeError("teach pendant backend not started")
    return client


def _joints(st) -> list[float]:
    return [j.position for j in st.joints]


# ---------------------------------------------------------------------------
# 手动示波器：常驻采样线程，把控制器关节位置写入 live 流（SSE 实时曲线数据源）
# ---------------------------------------------------------------------------
def _touch_manual() -> None:
    """更新手动活跃标记（mtime 新鲜度；控制台据此判定手动监控在线）。"""
    try:
        REPORTS.mkdir(parents=True, exist_ok=True)
        now = time.time()
        if MANUAL_ACTIVE.is_file():
            # touch：仅更新 mtime，避免 open("w") 竞态清空
            os.utime(MANUAL_ACTIVE, (now, now))
        else:
            MANUAL_ACTIVE.write_text("1", encoding="utf-8")
    except OSError:
        pass


def _write_manual_segment(buf: list[tuple[float, list[float]]], t0: float) -> None:
    """一批采样点 → 一行 JSON 段（与控制台 SSE 段格式一致，前端零改动渲染）。"""
    if not buf:
        return
    seg = {
        "case": "manual-jog",
        "n": len(buf),
        "t_ms": [(t - t0) * 1e3 for t, _ in buf],
        "q": [q for _, q in buf],
    }
    try:
        REPORTS.mkdir(parents=True, exist_ok=True)
        if LIVE_ACTIVE.is_file() and LIVE_ACTIVE.stat().st_size > MANUAL_MAX_BYTES:
            LIVE_ACTIVE.write_text("", encoding="utf-8")   # 轮转保护：防长挂爆盘
        with LIVE_ACTIVE.open("a", encoding="utf-8") as f:
            f.write(json.dumps(seg, ensure_ascii=False) + "\n")
    except OSError:
        pass


def _clear_manual_active() -> None:
    """清除手动活跃标记（线程空闲退出 / 进程退出时调用）。"""
    try:
        MANUAL_ACTIVE.unlink()
    except BaseException:  # noqa: BLE001
        pass


def _sample_loop(stop: threading.Event) -> None:
    """50Hz 读控制器关节位置，每 MANUAL_BATCH_MS 攒一批写一段 live 流。

    空闲自动停止：连续 MANUAL_IDLE_SEC 秒没有 jog 操作，线程退出并清除活跃
    标记 → SSE 收尾 → 前端页面定格（不操作时画面静止，不再无限增长）。
    """
    period = 1.0 / MANUAL_HZ
    buf: list[tuple[float, list[float]]] = []
    t0 = time.time()
    while not stop.is_set():
        if time.time() - _last_jog_ts > MANUAL_IDLE_SEC:
            _clear_manual_active()
            break
        _touch_manual()
        try:
            st = _require().get_state()
            joints = _joints(st)
        except BaseException:  # noqa: BLE001  agent 未就绪/瞬时 RPC 失败：跳过该周期
            joints = None
        if joints is not None:
            now = time.time()
            if not buf:
                t0 = now
            buf.append((now, joints))
            if (now - t0) * 1e3 >= MANUAL_BATCH_MS:
                _write_manual_segment(buf, t0)
                buf = []
        stop.wait(period)


def _ensure_manual_sampling() -> None:
    """幂等启动手动采样线程（第一次 jog 激活；进程存活期间常驻）。"""
    global _sampler, _sample_stop
    if _sampler is not None and _sampler.is_alive():
        return
    _sample_stop = threading.Event()
    _sampler = threading.Thread(target=_sample_loop, args=(_sample_stop,),
                                name="manual-sampler", daemon=True)
    _sampler.start()


def _stop_manual_sampling() -> None:
    """停止采样线程并清理活跃标记（进程退出路径）。"""
    global _sampler
    if _sample_stop is not None:
        _sample_stop.set()
    if _sampler is not None:
        _sampler.join(timeout=2.0)
        _sampler = None
    _clear_manual_active()


def _manual_active() -> bool:
    """手动监控是否活跃：采样线程 touch 标记文件，超过新鲜度阈值视为已停止。"""
    try:
        st = MANUAL_ACTIVE.stat()
    except OSError:
        return False
    return (time.time() - st.st_mtime) < MANUAL_FRESH_SEC


def _live_stream_gen(live: Path, is_active) -> str:
    """SSE 生成器（与 console 同语义）：增量推送 live 文件新增行。

    手动监控激活期间保持连接；标记过期且无新数据 → 断开。
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


@app.get("/api/live/active")
def live_active():
    """手动监控活跃状态（示教器前端 2s 心跳探测后连 SSE）。"""
    return {"active": _manual_active()}


@app.get("/api/live/stream")
def live_stream():
    """SSE 实时曲线（示教器页内嵌）：手动 jog / 运行中逐段推送关节位置。"""
    return StreamingResponse(
        _live_stream_gen(LIVE_ACTIVE, _manual_active),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


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
    global _last_jog_ts
    _last_jog_ts = time.time()   # 空闲判定：jog 操作刷新最后活动时刻
    # 手动示波器：第一次 jog 起激活采样线程，关节位置实时写入 live 流
    _ensure_manual_sampling()
    step = req.step_deg if (req.step_deg and req.step_deg > 0) else JOG_STEP_DEG
    delta = [0.0] * DOF
    delta[req.joint] = step * (1 if req.direction >= 0 else -1)
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


@app.post("/api/home")
def home():
    c = _require()
    r = c.home()
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
