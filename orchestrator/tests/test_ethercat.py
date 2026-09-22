"""P1-2 用例：EtherCAT 虚拟总线（周期 PDO 交换 + 状态机联动 + jitter）。

覆盖：总线信息 / 单周期 IO 交换驱动状态机 / 目标位置反馈（0x607A->0x6064）
      / 与 Cia402Service 共享从站状态（故障联动）/ RunCycles 真实计时
      与周期抖动 / 非法从站号拒绝。

设计：总线只是"通道"，从站状态由 CiA402 状态机决定——测的正是
      "周期 IO 与状态机联动"（写 0x6040 -> 迁移 -> 读 0x6041 反映）。
"""
from __future__ import annotations

import pytest

from app.client import ControllerClient
from tests import capture
from tests.test_cia402 import (_enable, _reset_slave, CW_ENABLE_OPERATION,
                               CW_SHUTDOWN, CW_SWITCH_ON, N_SLAVES,
                               SW_OPERATION_ENABLED, SW_READY_TO_SWITCH_ON,
                               SW_SWITCHED_ON, SW_FAULT)

CW = {
    "shutdown": CW_SHUTDOWN,
    "switch_on": CW_SWITCH_ON,
    "enable": CW_ENABLE_OPERATION,
}


def _out(slave: int, controlword: int, target: int = 0) -> dict:
    return {"slave_id": slave, "controlword": controlword,
            "target_position": target}


# ---------------------------------------------------------------------------
# 1. 总线信息
# ---------------------------------------------------------------------------
def test_bus_info(client):
    r = client.bus_info()
    assert r.ok and r.slave_count == N_SLAVES and r.cycle_hz == 1000, r
    capture.DETAILS["test_bus_info"] = (
        f"Virtual EtherCAT bus: {r.slave_count} slaves @ {r.cycle_hz} Hz")


# ---------------------------------------------------------------------------
# 2. 单周期 IO 交换驱动状态机（标准上电序列经总线）
# ---------------------------------------------------------------------------
def test_cycle_exchange_enable_sequence(client):
    _reset_slave(client, 0)
    r = client.cycle_exchange([_out(0, CW["shutdown"])])
    assert r.ok and r.inputs[0].state == "ReadyToSwitchOn", r
    assert r.inputs[0].statusword == SW_READY_TO_SWITCH_ON, hex(r.inputs[0].statusword)
    r = client.cycle_exchange([_out(0, CW["switch_on"])])
    assert r.inputs[0].state == "SwitchedOn", r
    assert r.inputs[0].statusword == SW_SWITCHED_ON, hex(r.inputs[0].statusword)
    r = client.cycle_exchange([_out(0, CW["enable"])])
    assert r.inputs[0].state == "OperationEnabled", r
    assert r.inputs[0].statusword == SW_OPERATION_ENABLED, hex(r.inputs[0].statusword)
    capture.DETAILS["test_cycle_exchange_enable_sequence"] = (
        "总线写 0x6040: 0x06->0x07->0x0F，输入 PDO 状态字逐周期同步")


# ---------------------------------------------------------------------------
# 3. 目标位置反馈（0x607A -> 0x6064 虚拟位置环直通）
# ---------------------------------------------------------------------------
def test_cycle_target_position_feedback(client):
    _reset_slave(client, 1)
    r = client.cycle_exchange([_out(1, CW["enable"], target=4500)])
    # 未使能完成序列（0x0F 非法），但目标位置仍应直通
    assert r.ok, r
    r2 = client.cycle_exchange([_out(1, CW["shutdown"], target=4500)])
    r2 = client.cycle_exchange([_out(1, CW["switch_on"], target=4500)])
    r2 = client.cycle_exchange([_out(1, CW["enable"], target=4500)])
    assert r2.inputs[0].state == "OperationEnabled", r2
    assert r2.inputs[0].actual_position == 4500, r2.inputs[0]
    capture.DETAILS["test_cycle_target_position_feedback"] = (
        "写 0x607A=4500 -> 输入 PDO 0x6064=4500（虚拟位置环直通）")


# ---------------------------------------------------------------------------
# 4. 总线与 Cia402Service 共享从站状态（故障联动）
# ---------------------------------------------------------------------------
def test_bus_reflects_fault_from_cia402(client):
    _enable(client, 2)
    client.inject_fault(2)                     # 经 Cia402Service 注入
    r = client.cycle_exchange([_out(2, 0x00)]) # 经总线读
    assert r.ok and r.inputs[0].state == "Fault", r
    assert r.inputs[0].statusword == SW_FAULT, hex(r.inputs[0].statusword)
    capture.DETAILS["test_bus_reflects_fault_from_cia402"] = (
        "Cia402Service 注入故障，总线输入 PDO 读到 Fault(0x0228)")


# ---------------------------------------------------------------------------
# 5. 多从站单周期交换（一条 outputs 写多个从站）
# ---------------------------------------------------------------------------
def test_cycle_exchange_multi_slave(client):
    _reset_slave(client, 3)
    _reset_slave(client, 4)
    r = client.cycle_exchange([
        _out(3, CW["shutdown"]),
        _out(4, CW["shutdown"]),
    ])
    assert r.ok and len(r.inputs) == 2, r
    assert {i.slave_id for i in r.inputs} == {3, 4}
    assert all(i.state == "ReadyToSwitchOn" for i in r.inputs), r.inputs
    capture.DETAILS["test_cycle_exchange_multi_slave"] = (
        "单周期同时交换 slave3/4，状态字均同步")


# ---------------------------------------------------------------------------
# 6. RunCycles：真实计时 + 周期抖动统计（sanity 断言，避免脆值）
# ---------------------------------------------------------------------------
def test_run_cycles_jitter_sanity(client):
    _enable(client, 5)
    r = client.run_cycles([_out(5, CW["enable"])], cycles=80, cycle_hz=500)
    assert r.ok, r
    assert r.cycles == 80
    assert 400 <= r.actual_hz <= 600, f"actual_hz={r.actual_hz}"
    assert r.jitter_max_us > 0.0, r
    assert 0.0 <= r.jitter_std_us <= r.jitter_max_us, r
    # 恒定 0x0F：OperationEnabled 自环，首末输入状态一致
    assert r.first_inputs[0].state == "OperationEnabled", r.first_inputs
    assert r.last_inputs[0].state == "OperationEnabled", r.last_inputs
    capture.DETAILS["test_run_cycles_jitter_sanity"] = (
        f"80 cycles @500Hz: actual={r.actual_hz:.0f}Hz "
        f"jitter max={r.jitter_max_us:.0f}us mean={r.jitter_mean_us:.0f}us "
        f"std={r.jitter_std_us:.0f}us")


# ---------------------------------------------------------------------------
# 7. 非法从站号拒绝
# ---------------------------------------------------------------------------
def test_invalid_slave_output_rejected(client):
    r = client.cycle_exchange([_out(99, 0x00)])
    assert not r.ok, r
    r2 = client.run_cycles([_out(99, 0x00)], cycles=2, cycle_hz=100)
    assert not r2.ok, r2
    capture.DETAILS["test_invalid_slave_output_rejected"] = (
        "outputs 含 slave 99 -> 单周期/多周期均拒绝")
