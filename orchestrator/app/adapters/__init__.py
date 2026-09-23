"""Adapter 选择与连接入口（P2.0）。

用法：
  cfg = adapter_config(profile)          # {"type": "simulation"|"hil", "endpoint": ...}
  adapter = make_adapter(cfg, client)    # simulation：包装已连接的 ControllerClient
  adapter = connect_hil(profile)         # hil：直接连接远端 HIL 盒子

验收语义（见 docs/p2-design.md §3.3）：
  - 用例层不感知底层（Simulation/HIL 同一套 RobotAdapter 接口）；
  - hil 未配置 endpoint / 不可达时抛清晰错误，不得挂死或静默回退。
"""
from __future__ import annotations

from app.adapters.base import RobotAdapter
from app.adapters.grpc_adapter import GrpcAdapter

VALID_TYPES = ("simulation", "hil")


def adapter_config(profile) -> dict:
    """从 RobotProfile 解析 adapter 配置；非法类型 / HIL 缺 endpoint 直接报错。"""
    t = (getattr(profile, "adapter_type", "simulation") or "simulation").strip().lower()
    if t not in VALID_TYPES:
        raise ValueError(
            f"未知 adapter 类型 {t!r}（可用: {', '.join(VALID_TYPES)}，"
            f"Profile: {profile.name}）")
    endpoint = getattr(profile, "adapter_endpoint", None)
    if t == "hil" and not endpoint:
        raise ValueError(
            f"HIL adapter 需要 endpoint（请在 {profile.source_path} 的 "
            f"adapter.endpoint 配置，如 192.168.1.10:50051）")
    return {"type": t, "endpoint": endpoint}


def make_grpc_adapter(client, profile) -> GrpcAdapter:
    """把已连接的 ControllerClient 包装为 RobotAdapter（simulation 路径）。"""
    return GrpcAdapter(client, profile)


def connect_hil(profile, timeout_s: float = 15.0) -> GrpcAdapter:
    """连接真实 HIL 盒子（hil 路径）：不可达时抛清晰错误。"""
    from app.client import ControllerClient

    cfg = adapter_config(profile)
    endpoint = cfg["endpoint"]
    c = ControllerClient(endpoint)
    if not c.wait_ready(timeout_s=timeout_s):
        err = getattr(c, "_last_ready_error", "")
        c.close()
        raise RuntimeError(
            f"HIL 未就绪：{endpoint}（最后错误: {err}）。请检查 HIL 盒子电源/网络/"
            f"controller_agent 进程，或改用 adapter.type: simulation。")
    return GrpcAdapter(c, profile)


__all__ = ["RobotAdapter", "GrpcAdapter", "adapter_config",
           "make_grpc_adapter", "connect_hil"]
