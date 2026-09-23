"""示教器协议桥（P2.3 路径 B）——TP/1.0 TCP 服务器 ↔ 控制器 gRPC。

桥的角色：真实示教器接入点在测试架构里的"协议契约层"。任何示教器客户端
（浏览器 UI 走 REST、协议模拟器走本 TCP 线、未来真示教器经厂商桥）都
归结为同一组控制器操作；本桥把 TP 帧翻译成 RobotAdapter 调用，并把
控制器状态回填成响应/推送帧。

边界：本模块是协议仿真层，华沿私有示教器协议未公开（需厂商资料），
厂商报文到本协议的映射由未来 RealTPBridge 承担（docs/p2-design.md §6）。
"""
from __future__ import annotations

import json
import logging
import socketserver
import threading
import time
from typing import Optional

from app.adapters.base import RobotAdapter
from teach_pendant.protocol import (
    TPProtocolError,
    FrameStream,
    decode_frame,
    encode_error,
    encode_request,
    encode_response,
    encode_state,
)

log = logging.getLogger("tp_bridge")

# 操作语义与 Virtual Teach Pendant REST 一致（UI/协议两条线共享契约）：
#   JOG: 先普通停止再增量运动（避免 BUSY 拒绝）；STOP emergency 走急停。
VERBS = {"SERVO", "JOG", "STOP", "STATE", "RESET", "HOME", "QUIT"}


def _joints(st) -> list[float]:
    return [round(j.position, 4) for j in st.joints]


def _state_payload(st) -> dict:
    return {
        "enabled": bool(st.enabled),
        "moving": bool(st.moving),
        "motion_state": st.motion_state,
        "joints": _joints(st),
        "error_code": st.error_code,
        "error_message": st.error_message,
    }


class _Handler(socketserver.StreamRequestHandler):
    """每连接一线程：读取 TP 帧 → 分派 → 回写响应；另起推送线程发 STATE。"""

    def handle(self) -> None:  # noqa: D102
        server: TPBridgeServer = self.server  # type: ignore[assignment]
        self._stop_push = threading.Event()
        push = threading.Thread(target=self._push_loop, daemon=True)
        push.start()
        fs = server.fs_cls()
        try:
            while True:
                chunk = self.rfile.readline().decode("utf-8", "replace")
                if not chunk:
                    break
                for line in fs.feed(chunk):
                    self._dispatch(line)
        except (ConnectionError, OSError):
            pass
        finally:
            self._stop_push.set()

    def _dispatch(self, line: str) -> None:
        server: TPBridgeServer = self.server  # type: ignore[assignment]
        try:
            kind, payload = decode_frame(line)
        except TPProtocolError as e:
            self.wfile.write(encode_error(1, f"bad-frame {e}").encode())
            self.wfile.flush()
            return
        if kind == "request":
            verb, args = payload
            out = server.dispatch(verb, args)
            self.wfile.write(out.encode())
            self.wfile.flush()
        else:
            # 桥是服务器：客户端不应主动发 OK/ERR/STATE
            self.wfile.write(encode_error(2, "unexpected-frame").encode())
            self.wfile.flush()

    def _push_loop(self) -> None:
        server: TPBridgeServer = self.server  # type: ignore[assignment]
        try:
            while not self._stop_push.wait(0.5):
                try:
                    payload = server.snapshot_state()
                    self.wfile.write(encode_state(payload).encode())
                    self.wfile.flush()
                except (ConnectionError, OSError):
                    return
        except Exception:  # noqa: BLE001
            return


class TPBridgeServer(socketserver.ThreadingTCPServer):
    """TP/1.0 协议桥：TCP 帧 ↔ RobotAdapter（控制器 gRPC）。

    allow_reuse_address 便于测试快速重建。
    """

    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, host: str, port: int, adapter: RobotAdapter,
                 state_push: bool = True, fs_cls=FrameStream):
        self.adapter = adapter
        self.state_push = state_push
        self.fs_cls = fs_cls
        super().__init__((host, port), _Handler)

    @property
    def port(self) -> int:
        return self.server_address[1]

    def dispatch(self, verb: str, args: dict) -> str:
        """执行协议动词，返回 TP 响应/错误帧（含控制器错误信息）。"""
        try:
            return self._do(verb, args)
        except Exception as e:  # noqa: BLE001
            log.warning("dispatch %s failed: %s", verb, e)
            return encode_error(4, f"internal {e}")

    def _do(self, verb: str, args: dict) -> str:
        a = self.adapter
        if verb == "SERVO":
            r = a.enable() if bool(args.get("on", True)) else a.disable()
            return _cmd_frame(r, {"enabled": bool(args.get("on", True))})
        if verb == "JOG":
            axis = int(args.get("axis", 0))
            direction = int(args.get("dir", 1))
            speed = float(args.get("speed", 30.0))
            a.stop(emergency=False)
            delta = [0.0] * 7
            delta[axis] = 5.0 * direction
            r = a.move_relative(delta, velocity=speed)
            return _cmd_frame(r, {"axis": axis, "dir": direction})
        if verb == "STOP":
            r = a.stop(emergency=bool(args.get("emergency", False)))
            return _cmd_frame(r, {"stopped": True})
        if verb == "STATE":
            return encode_response(self.snapshot_state())
        if verb == "RESET":
            r = a.reset()
            return _cmd_frame(r, {"reset": True})
        if verb == "HOME":
            r = a.home(velocity=float(args.get("speed", 30.0)))
            return _cmd_frame(r, {"homed": True})
        if verb == "QUIT":
            return encode_response({"bye": True})
        return encode_error(3, f"unknown-verb {verb}")

    def snapshot_state(self) -> dict:
        return _state_payload(self.adapter.get_state())


def _cmd_frame(r, payload: dict) -> str:
    if getattr(r, "ok", False):
        return encode_response(payload)
    return encode_error(5, f"{r.error_message or 'command failed'} (code={r.error_code})")

