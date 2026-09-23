"""P2.3 用例：示教器协议模拟器（TP/1.0 协议桥 + 协议客户端）。

覆盖（纯软件，CI 可跑）：
  - 协议编解码往返 / 坏帧拒绝 / 粘包半包重组（纯单测，不起进程）；
  - 协议桥集成：SERVO/JOG/STOP 经 TP 线**真实改变控制器状态**——与
    Virtual Teach Pendant UI 用例同款语义（操作 → 控制器状态变化），
    双客户端共享控制器契约（docs/p2-design.md §6.2）；
  - STATE 推送帧 / 未知动词拒绝 / 控制器错误透传。

边界：TP/1.0 为自研仿真协议（华沿私有协议未公开）；厂商报文到本协议的
映射由未来 RealTPBridge 承担，本层只验证"协议线 → 控制器"契约。
"""
from __future__ import annotations

import socket
import threading
import time

import pytest

from teach_pendant.protocol import (
    TPProtocolError,
    FrameStream,
    decode_frame,
    encode_error,
    encode_request,
    encode_response,
    encode_state,
)
from teach_pendant.tp_bridge import TPBridgeServer
from teach_pendant.tp_simulator import TPSimulator

from tests import capture


# ---------------------------------------------------------------------------
# 1. 协议编解码（纯单测）
# ---------------------------------------------------------------------------
def test_protocol_roundtrip():
    assert decode_frame(encode_request("JOG", {"axis": 0, "dir": 1})) == \
        ("request", ("JOG", {"axis": 0, "dir": 1}))
    assert decode_frame(encode_request("STATE")) == ("request", ("STATE", {}))
    assert decode_frame(encode_response({"a": 1})) == ("ok", {"a": 1})
    assert decode_frame(encode_error(3, "unknown-verb X")) == ("err", (3, "unknown-verb X"))
    assert decode_frame(encode_state({"enabled": True})) == ("state", {"enabled": True})
    capture.DETAILS["test_protocol_roundtrip"] = "TP/1.0 帧编解码往返一致"


def test_protocol_bad_frame_rejected():
    for bad in ["", "HTTP/1.1 200 OK", "TP/1.0 OK {bad json", "TP/1.0 ERR"]:
        with pytest.raises(TPProtocolError):
            decode_frame(bad)
    capture.DETAILS["test_protocol_bad_frame_rejected"] = "坏帧（版本/JSON/缺码）被拒绝"


def test_protocol_stream_split_and_coalesce():
    fs = FrameStream()
    req = encode_request("STATE")
    half = len(req) // 2
    assert fs.feed(req[:half]) == []
    assert fs.feed(req[half:]) == [req.strip("\n")]  # 半包重组
    fs2 = FrameStream()
    frames = fs2.feed(encode_request("STATE") + encode_response({"x": 1}))
    assert len(frames) == 2  # 粘包拆分
    capture.DETAILS["test_protocol_stream_split_and_coalesce"] = "TCP 半包/粘包正确重组"


# ---------------------------------------------------------------------------
# 2. 协议桥集成（TP 客户端 → 桥 → 控制器，断言控制器真实变化）
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def tp_bridge(client):
    server = TPBridgeServer("127.0.0.1", 0, client, state_push=False)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    yield server.port
    server.shutdown()
    server.server_close()


@pytest.fixture()
def tp(tp_bridge):
    sim = TPSimulator("127.0.0.1", tp_bridge)
    yield sim
    sim.close()


def test_tp_servo_on_controls_controller(tp, client):
    tp.servo_on()
    st = client.get_state()
    assert st.enabled is True, st
    tp.servo_off()
    assert client.get_state().enabled is False
    tp.servo_on()  # 恢复使能，避免影响后续用例
    capture.DETAILS["test_tp_servo_on_controls_controller"] = (
        "TP SERVO on/off 经协议桥真实改变控制器使能状态")


def test_tp_jog_moves_controller_joint(tp, client):
    tp.servo_on()
    before = client.get_state().joints[0].position
    r = tp.jog(axis=0, direction=1, speed=30.0)
    assert r == {"axis": 0, "dir": 1}, r
    for _ in range(60):
        if abs(client.get_state().joints[0].position - before - 5.0) < 0.2:
            break
        time.sleep(0.1)
    got = client.get_state().joints[0].position
    assert abs(got - before - 5.0) < 0.2, f"joint0 {before:.2f} -> {got:.2f}"
    capture.DETAILS["test_tp_jog_moves_controller_joint"] = (
        f"TP JOG+ -> 控制器 joints[0] {before:.1f}° -> {before + 5:.1f}°（协议线真实生效）")


def test_tp_stop_during_motion(tp, client):
    tp.servo_on()
    r0 = client.move_absolute([90.0] + [0.0] * 6, velocity=30.0, abort_current=True)
    assert r0.ok, r0.error_message
    time.sleep(0.2)
    r = tp.stop()
    assert r == {"stopped": True}, r
    for _ in range(60):
        if not client.get_state().moving:
            break
        time.sleep(0.1)
    assert client.get_state().moving is False
    capture.DETAILS["test_tp_stop_during_motion"] = "TP STOP 经协议桥停止控制器运动"


def test_tp_unknown_verb_rejected(tp):
    payload = tp.send("HOP")
    assert payload[0] == 3, payload
    assert "unknown-verb" in payload[1]
    capture.DETAILS["test_tp_unknown_verb_rejected"] = "未知动词 -> ERR unknown-verb"


def test_tp_controller_error_passthrough(tp):
    """控制器错误经协议桥透传：未使能时 JOG 被拒（NOT_ENABLED）。"""
    tp.servo_off()
    payload = tp.jog(axis=0, direction=1)
    assert payload[0] == 5, payload
    assert "not enabled" in payload[1], payload
    tp.servo_on()
    capture.DETAILS["test_tp_controller_error_passthrough"] = (
        "控制器错误（NOT_ENABLED）经协议桥 ERR 透传")


def test_tp_state_push_feed(client):
    """连接后周期性 STATE 推送帧（含 joints/enabled）。"""
    server = TPBridgeServer("127.0.0.1", 0, client, state_push=True)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    try:
        s = socket.create_connection(("127.0.0.1", server.port), timeout=5)
        data = b""
        for _ in range(40):
            data += s.recv(4096)
            if b"TP/1.0 STATE" in data:
                break
            time.sleep(0.1)
        s.close()
        assert b"TP/1.0 STATE" in data, data[:200]
        assert b"joints" in data and b"enabled" in data, data[:200]
    finally:
        server.shutdown()
        server.server_close()
    capture.DETAILS["test_tp_state_push_feed"] = "协议桥周期性推送 STATE 帧（joints/enabled）"
