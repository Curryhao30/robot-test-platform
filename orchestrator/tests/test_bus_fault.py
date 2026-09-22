"""P1-5 用例：EtherCAT 总线异常注入（通信断开 / 从站丢失 / 总线错误）。

覆盖：SlaveLoss 从站隔离（其余从站照常）、LinkLoss 整线不可用、
      BusError 帧级失败、注入-恢复循环、RunCycles 在故障下的行为。

语义对齐真实 EtherCAT 主站：
  - 从站丢失：watchdog 超时，该从站输入无效（LOST），其余从站继续交换
  - 通信断开：整条链路不可用，任何交换失败
  - 总线错误：帧级错误（CRC/看门狗），交换失败
"""
from __future__ import annotations

import pytest

from tests import capture
from tests.test_ethercat import _out, CW

LINK_LOSS = 1
SLAVE_LOSS = 2
BUS_ERROR = 3


@pytest.fixture(autouse=True)
def _clean_bus(client):
    """每个用例前后清故障；结束后把用到的从站复位到 SwitchOnDisabled，
    避免污染后续测试（test_cia402 的初始态断言）。"""
    client.clear_bus_fault()
    yield
    client.clear_bus_fault()
    from tests.test_cia402 import _reset_slave
    for s in range(7):
        try:
            _reset_slave(client, s)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# 1. 从站丢失：该从站无响应（LOST），其余从站照常
# ---------------------------------------------------------------------------
def test_slave_loss_marks_lost_and_isolates(client):
    # 使能 slave0/slave1 到 OperationEnabled（经总线）
    client.cycle_exchange([_out(0, CW["shutdown"]), _out(1, CW["shutdown"])])
    client.cycle_exchange([_out(0, CW["switch_on"]), _out(1, CW["switch_on"])])
    client.cycle_exchange([_out(0, CW["enable"]), _out(1, CW["enable"])])

    r = client.inject_bus_fault(SLAVE_LOSS, slave_id=1)
    assert r.ok, r
    # 含丢失从站的交换：slave1 LOST，slave0 正常
    r = client.cycle_exchange([_out(0, CW["enable"]), _out(1, CW["enable"])])
    assert r.ok, r
    by_id = {i.slave_id: i for i in r.inputs}
    assert by_id[1].lost is True and by_id[1].state == "LOST", by_id[1]
    assert by_id[0].lost is False and by_id[0].state == "OperationEnabled", by_id[0]
    capture.DETAILS["test_slave_loss_marks_lost_and_isolates"] = (
        "注入 slave1 丢失：slave1=LOST 无响应，slave0 仍 OperationEnabled")


# ---------------------------------------------------------------------------
# 2. 从站丢失后恢复
# ---------------------------------------------------------------------------
def test_slave_loss_recovery(client):
    client.cycle_exchange([_out(2, CW["shutdown"])])
    r = client.inject_bus_fault(SLAVE_LOSS, slave_id=2)
    assert r.ok, r
    r = client.cycle_exchange([_out(2, CW["shutdown"])])
    assert r.inputs[0].lost is True, r.inputs[0]
    r = client.clear_bus_fault()
    assert r.ok, r
    r = client.cycle_exchange([_out(2, CW["shutdown"])])
    assert r.inputs[0].lost is False and r.inputs[0].state == "ReadyToSwitchOn", r.inputs[0]
    capture.DETAILS["test_slave_loss_recovery"] = (
        "清除故障后 slave2 恢复交换（ReadyToSwitchOn）")


# ---------------------------------------------------------------------------
# 3. 通信断开：整条总线不可用
# ---------------------------------------------------------------------------
def test_link_loss_blocks_exchange(client):
    r = client.inject_bus_fault(LINK_LOSS)
    assert r.ok, r
    r = client.cycle_exchange([_out(0, CW["shutdown"])])
    assert not r.ok and "LINK_LOSS" in r.error, r
    capture.DETAILS["test_link_loss_blocks_exchange"] = (
        "LINK_LOSS 下任何交换失败（error 含 LINK_LOSS）")


def test_link_loss_blocks_run_cycles(client):
    client.inject_bus_fault(LINK_LOSS)
    r = client.run_cycles([_out(0, CW["enable"])], cycles=5, cycle_hz=100)
    assert not r.ok and "LINK_LOSS" in r.error, r
    capture.DETAILS["test_link_loss_blocks_run_cycles"] = (
        "RunCycles 在 LINK_LOSS 下失败并透传错误")


# ---------------------------------------------------------------------------
# 4. 总线错误：帧级失败
# ---------------------------------------------------------------------------
def test_bus_error_fails_exchange(client):
    r = client.inject_bus_fault(BUS_ERROR)
    assert r.ok, r
    r = client.cycle_exchange([_out(0, CW["shutdown"])])
    assert not r.ok and "BUS_ERROR" in r.error, r
    r = client.clear_bus_fault()
    assert r.ok, r
    r = client.cycle_exchange([_out(0, CW["shutdown"])])
    assert r.ok and r.inputs[0].state == "ReadyToSwitchOn", r
    capture.DETAILS["test_bus_error_fails_exchange"] = (
        "BUS_ERROR 下交换失败；清除后恢复")


# ---------------------------------------------------------------------------
# 5. 故障注入-清除循环（每种类型依次验证可恢复）
# ---------------------------------------------------------------------------
def test_fault_inject_clear_cycle(client):
    for ft, name in [(LINK_LOSS, "LINK_LOSS"), (SLAVE_LOSS, "SLAVE_LOSS"),
                     (BUS_ERROR, "BUS_ERROR")]:
        r = client.inject_bus_fault(ft, slave_id=3)
        assert r.ok, (ft, r)
        r = client.clear_bus_fault()
        assert r.ok, (ft, r)
    r = client.cycle_exchange([_out(3, CW["shutdown"])])
    assert r.ok and r.inputs[0].state == "ReadyToSwitchOn", r
    capture.DETAILS["test_fault_inject_clear_cycle"] = (
        "三种故障类型注入-清除循环后总线恢复正常")


# ---------------------------------------------------------------------------
# 6. 非法注入参数拒绝
# ---------------------------------------------------------------------------
def test_invalid_fault_slave_rejected(client):
    r = client.inject_bus_fault(SLAVE_LOSS, slave_id=99)
    assert not r.ok, r
    r = client.inject_bus_fault(99)  # 未知类型
    assert not r.ok, r
    capture.DETAILS["test_invalid_fault_slave_rejected"] = (
        "非法从站号 / 未知故障类型被拒绝")
