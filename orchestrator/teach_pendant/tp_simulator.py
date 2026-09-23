"""示教器协议模拟器（P2.3 路径 B）——TP/1.0 客户端，替代浏览器 UI。

模拟真实示教器的操作行为：操作员按键（Servo ON / Jog+ / Stop / Reset）被
编码成 TP 帧发往协议桥，桥再落到控制器 gRPC。本模块与浏览器 UI 是
**两条独立的示教器客户端**，共享同一控制器契约（docs/p2-design.md §6.2）。

CLI 演示:
    python -m teach_pendant.tp_simulator --bridge 127.0.0.1:55111 --script jog_demo
"""
from __future__ import annotations

import argparse
import socket
import time
from typing import Any

from teach_pendant.protocol import (
    FrameStream,
    TPProtocolError,
    decode_frame,
    encode_request,
)


class TPSimulator:
    """TP 协议客户端：向协议桥发起示教器操作并读响应。"""

    def __init__(self, host: str, port: int, timeout: float = 5.0) -> None:
        self._sock = socket.create_connection((host, port), timeout=timeout)
        self._sock.settimeout(timeout)
        self._fs = FrameStream()

    def close(self) -> None:
        self._sock.close()

    def send(self, verb: str, args: dict | None = None) -> Any:
        """发送一帧并等待同帧响应的下一帧（跳过 STATE 推送）。"""
        self._sock.sendall(encode_request(verb, args).encode("utf-8"))
        return self.recv_response()

    def recv_response(self) -> Any:
        while True:
            kind, payload = self._read()
            if kind == "request":
                continue  # 桥不应回请求；忽略防御
            if kind == "state":
                continue  # 推送帧，跳过直到响应
            return payload

    def _read(self) -> tuple[str, Any]:
        while True:
            chunk = self._sock.recv(4096).decode("utf-8", "replace")
            if not chunk:
                raise ConnectionError("TP 连接关闭")
            for line in self._fs.feed(chunk):
                try:
                    return decode_frame(line)
                except TPProtocolError:
                    continue

    # -- 示教器操作（语义与 UI 按钮一致）-----------------------------------
    def servo_on(self) -> Any:
        return self.send("SERVO", {"on": True})

    def servo_off(self) -> Any:
        return self.send("SERVO", {"on": False})

    def jog(self, axis: int, direction: int = 1, speed: float = 30.0) -> Any:
        return self.send("JOG", {"axis": axis, "dir": direction, "speed": speed})

    def stop(self, emergency: bool = False) -> Any:
        return self.send("STOP", {"emergency": emergency})

    def reset(self) -> Any:
        return self.send("RESET")

    def home(self, speed: float = 30.0) -> Any:
        return self.send("HOME", {"speed": speed})

    def state(self) -> dict:
        return self.send("STATE")


# 演示脚本：模拟操作员一次"使能 → Jog+ → 读状态 → 停止"操作序列
DEMO_SCRIPTS = {
    "jog_demo": [
        ("servo_on", {}),
        ("jog", {"axis": 0, "dir": 1, "speed": 30}),
        ("state", {}),
        ("stop", {}),
    ],
}


def main() -> None:
    ap = argparse.ArgumentParser(description="TP/1.0 示教器协议模拟器")
    ap.add_argument("--bridge", default="127.0.0.1:55111", help="协议桥 host:port")
    ap.add_argument("--script", default="jog_demo", choices=sorted(DEMO_SCRIPTS),
                    help="演示脚本")
    args = ap.parse_args()
    host, _, port_s = args.bridge.rpartition(":")
    sim = TPSimulator(host or "127.0.0.1", int(port_s))
    try:
        for step, params in DEMO_SCRIPTS[args.script]:
            r = getattr(sim, step)(**params)
            print(f"[TP] {step} -> {r}")
            time.sleep(0.2)
    finally:
        sim.close()


if __name__ == "__main__":
    main()
