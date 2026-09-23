"""示教器协议（TP/1.0）编解码——P2.3 路径 B 协议模拟器核心。

设计：面向"示教器 ↔ 控制器"的行分隔 JSON 帧协议（自研仿真协议，
**非华沿私有协议**——真实厂商协议需技术资料，见 docs/p2-design.md §6）。

帧格式（每帧以 \\n 结尾，字段间单空格）：
  请求:  TP/1.0 <VERB> <json>\\n
  响应:  TP/1.0 OK <json>\\n
  错误:  TP/1.0 ERR <code> <message>\\n
  推送:  TP/1.0 STATE <json>\\n        （连接后周期性推送控制器状态）

VERB:  SERVO {"on": bool}      JOG  {"axis":int,"dir":1|-1,"speed":deg/s}
       STOP  {"emergency":bool} STATE {}  RESET {}  HOME {}  QUIT {}

只依赖标准库；解码严格（前缀 + JSON 合法才接受），坏帧抛 TPProtocolError。
"""
from __future__ import annotations

import json
from typing import Any

PROTO_VERSION = "TP/1.0"


class TPProtocolError(ValueError):
    """协议解析/编码错误。"""


def encode_request(verb: str, args: dict | None = None) -> str:
    """请求帧：'TP/1.0 <VERB> <json>\\n'（args 为空时省略 json 字段）。"""
    verb = verb.upper().strip()
    if not verb:
        raise TPProtocolError("verb 为空")
    if args:
        return f"{PROTO_VERSION} {verb} {json.dumps(args, separators=(',', ':'))}\n"
    return f"{PROTO_VERSION} {verb}\n"


def encode_response(payload: dict | None = None) -> str:
    """OK 响应帧：'TP/1.0 OK <json>\\n'。"""
    body = json.dumps(payload or {}, separators=(",", ":"))
    return f"{PROTO_VERSION} OK {body}\n"


def encode_error(code: int, message: str) -> str:
    """错误帧：'TP/1.0 ERR <code> <message>\\n'（message 不含空格/换行）。"""
    msg = str(message).replace("\n", " ").replace("\r", " ").strip()
    return f"{PROTO_VERSION} ERR {int(code)} {msg}\n"


def encode_state(payload: dict) -> str:
    """状态推送帧：'TP/1.0 STATE <json>\\n'。"""
    body = json.dumps(payload, separators=(",", ":"))
    return f"{PROTO_VERSION} STATE {body}\n"


def decode_frame(line: str) -> tuple[str, Any]:
    """解析一帧，返回 (kind, payload)。

    kind: 'request' (verb, args) / 'ok' (payload) / 'err' (code, message)
          / 'state' (payload)。
    非法帧（版本不符、JSON 损坏、缺字段）抛 TPProtocolError。
    """
    line = line.strip("\r\n")
    if not line:
        raise TPProtocolError("空帧")
    parts = line.split(" ", 2)
    if len(parts) < 2 or parts[0] != PROTO_VERSION:
        raise TPProtocolError(f"版本/结构非法: {line[:40]!r}")
    kind_word = parts[1].upper()
    rest = parts[2] if len(parts) > 2 else ""
    if kind_word == "OK":
        return ("ok", _loads(rest))
    if kind_word == "ERR":
        bits = rest.split(" ", 1)
        code = bits[0]
        if not code.isdigit():
            raise TPProtocolError(f"ERR 缺数字码: {line[:40]!r}")
        return ("err", (int(code), bits[1] if len(bits) > 1 else ""))
    if kind_word == "STATE":
        # 'TP/1.0 STATE {json}' = 服务器推送帧；'TP/1.0 STATE'（无 body）= 客户端请求
        if rest:
            return ("state", _loads(rest))
        return ("request", ("STATE", {}))
    # 请求帧：动词合法性由协议桥语义层判定（unknown-verb 由桥返回 ERR），
    # 解码层只负责帧结构（版本 + 动词 + 可选 JSON）。
    return ("request", (kind_word, _loads(rest) if rest else {}))


def _loads(s: str) -> Any:
    try:
        return json.loads(s) if s else {}
    except json.JSONDecodeError as e:
        raise TPProtocolError(f"JSON 非法: {s[:40]!r}") from e


class FrameStream:
    """TCP 流 → 帧：处理半包与粘包，按 '\\n' 拆分并逐帧产出。

    用法:
        fs = FrameStream()
        for line in fs.feed(chunk):   # chunk 可能含 0..N 帧
            kind, payload = decode_frame(line)
    """

    def __init__(self) -> None:
        self._buf = ""

    def feed(self, data: str) -> list[str]:
        self._buf += data
        frames = []
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            line = line.strip("\r")
            if line:
                frames.append(line)
        return frames
