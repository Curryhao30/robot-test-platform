"""P2.0 用例：Robot Adapter 抽象（纯配置解析，不连 Agent / 不连真机）。

覆盖：simulation 默认解析 / hil 缺 endpoint 报错 / 未知类型报错 /
      hil 未就绪时 connect_hil 抛清晰错误（不挂死、不静默回退）。
"""
from __future__ import annotations

import pytest

from app.adapters import adapter_config, connect_hil
from app.profile import RobotProfile

_YAML = """name: {name}
dof: 7
controller:
  cycle_hz: 1000
joints:
  - limits: {{ position: [-175, 175], velocity: 120, acceleration: 300, jerk: 1000 }}
accuracy:
  position_tolerance_deg: 0.01
{extra}"""


def _profile(tmp_path, name: str, extra: str) -> RobotProfile:
    p = tmp_path / f"{name}.yaml"
    p.write_text(_YAML.format(name=name, extra=extra), encoding="utf-8")
    return RobotProfile.from_file(p)


def test_adapter_config_simulation_default(tmp_path):
    """未配置 adapter -> simulation（默认），无 endpoint。"""
    p = _profile(tmp_path, "sim_default", "")
    assert p.adapter_type == "simulation" and p.adapter_endpoint is None
    assert adapter_config(p) == {"type": "simulation", "endpoint": None}


def test_adapter_config_simulation_explicit(tmp_path):
    p = _profile(tmp_path, "sim_exp", "adapter:\n  type: simulation")
    assert adapter_config(p)["type"] == "simulation"


def test_adapter_config_hil_requires_endpoint(tmp_path):
    """HIL 未配置 endpoint -> 明确报错（验收：不挂死、不静默回退）。"""
    p = _profile(tmp_path, "hil_noep", "adapter:\n  type: hil")
    with pytest.raises(ValueError, match="endpoint"):
        adapter_config(p)


def test_adapter_config_hil_with_endpoint(tmp_path):
    p = _profile(tmp_path, "hil_ep",
                 "adapter:\n  type: hil\n  endpoint: 192.168.1.10:50051")
    assert adapter_config(p) == {"type": "hil",
                                 "endpoint": "192.168.1.10:50051"}


def test_adapter_config_rejects_unknown(tmp_path):
    p = _profile(tmp_path, "unknown", "adapter:\n  type: quantum")
    with pytest.raises(ValueError, match="未知 adapter"):
        adapter_config(p)


def test_connect_hil_unreachable_raises_clear_error(tmp_path):
    """HIL 端点不可达 -> RuntimeError 含端点信息，不挂死。"""
    p = _profile(tmp_path, "hil_down",
                 "adapter:\n  type: hil\n  endpoint: 127.0.0.1:1")
    with pytest.raises(RuntimeError, match="HIL 未就绪|未就绪"):
        connect_hil(p, timeout_s=2.0)
