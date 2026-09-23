"""P2.1a 用例：SOEM EtherCAT 主站骨架——真机接入点与清晰失败语义。

覆盖（纯软件，无需真网卡 / 无需 SOEM 库）：
  - --bus soem 且网卡不存在/非 Linux -> agent FATAL 退出，日志含清晰原因；
  - --bus 非法值 -> 拒绝并说明可用值；
  - 默认 --bus virtual 行为不受影响（由全量回归覆盖）。

P2.1b 真机阶段：init() 接入 SOEM（ecx_init/扫描/PDO），本组用例语义不变
（失败原因从"未实现"变为"无从站/WKC 错误"）。
"""
from __future__ import annotations

import pathlib
import subprocess

import pytest

from app.client import spawn_agent

REPO = pathlib.Path(__file__).resolve().parents[2]
PROFILE = str(REPO / "robot_profiles" / "maira_sim.yaml")


def _spawn_soem(port: int, iface: str, log: str):
    return spawn_agent(PROFILE, port=port, log_file=log,
                       extra_args=["--bus", "soem", "--iface", iface])


def test_soem_missing_interface_fails_cleanly(tmp_path):
    """SOEM 模式 + 网卡不可用 -> FATAL 退出 + 日志含清晰错误（不挂死）。"""
    log = str(tmp_path / "soem_missing.log")
    proc = _spawn_soem(51111, "rtp_eth_zzz_not_exist", log)
    try:
        rc = proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
        pytest.fail("soem 模式 agent 未按预期退出（不应挂死）")
    out = ""
    try:
        out = pathlib.Path(log).read_text(encoding="utf-8", errors="replace")
    except OSError:
        pass
    assert rc != 0, f"soem 骨架应 FATAL 退出（rc={rc}）"
    assert "FATAL" in out, out
    assert ("EtherCAT" in out) or ("网卡" in out) or ("Linux" in out), out


def test_soem_missing_iface_log_has_actionable_hint(tmp_path):
    """失败日志包含可操作提示（查网卡名 / 换 virtual / 部署 Linux）。"""
    log = str(tmp_path / "soem_hint.log")
    proc = _spawn_soem(51112, "rtp_eth_zzz_not_exist", log)
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
        pytest.fail("soem 模式 agent 未按预期退出")
    out = pathlib.Path(log).read_text(encoding="utf-8", errors="replace")
    # Windows: 提示需 Linux；Linux: 提示网卡不存在/不可用
    assert any(k in out for k in ("--bus virtual", "eth0", "Linux", "网卡")), out


def test_unknown_bus_rejected(tmp_path):
    """--bus 非法值 -> 退出码 2 + 日志含可用值说明。"""
    log = str(tmp_path / "unknown_bus.log")
    proc = spawn_agent(PROFILE, port=51113, log_file=log,
                       extra_args=["--bus", "quantum"])
    try:
        rc = proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
        pytest.fail("未知 --bus 未按预期退出")
    out = pathlib.Path(log).read_text(encoding="utf-8", errors="replace")
    assert rc == 2, rc
    assert "virtual | soem" in out, out
