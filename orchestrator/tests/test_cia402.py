"""P1 用例：CiA402 驱动器状态机 + 虚拟从站（纯软件层）。

覆盖：上电初始态 / 标准迁移序列 / 非法迁移拒绝 / Quick Stop 循环 /
      Fault 注入与 Fault Reset / 对象字典读写 / 模式设置 / 只读保护 /
      多从站独立性 / 非法从站号。

设计：每个用例通过 _reset_slave 把从站复位到 SwitchOnDisabled，保证用例隔离；
      slave 索引 0..6 对应 7 个关节（MAiRA-like Profile）。
"""
from __future__ import annotations

import pytest

from app.adapters import RobotAdapter
from tests import capture

# CiA402 标准状态字（含 bit9 Remote=1）
SW_SWITCH_ON_DISABLED = 0x0250
SW_READY_TO_SWITCH_ON = 0x0221
SW_SWITCHED_ON = 0x0223
SW_OPERATION_ENABLED = 0x0227
SW_QUICK_STOP_ACTIVE = 0x0227
SW_FAULT_REACTION_ACTIVE = 0x022F
SW_FAULT = 0x0228

# 标准控制字命令（0x6040）
CW_DISABLE_VOLTAGE = 0x00
CW_QUICK_STOP = 0x02
CW_SHUTDOWN = 0x06
CW_SWITCH_ON = 0x07
CW_ENABLE_OPERATION = 0x0F
CW_FAULT_RESET = 0x80

# 对象字典索引
OBJ_CTRL = 0x6040
OBJ_STATUS = 0x6041
OBJ_MODE = 0x6060
OBJ_MODE_DISPLAY = 0x6061
OBJ_POS_ACTUAL = 0x6064
OBJ_TARGET_POS = 0x607A
OBJ_PROFILE_VEL = 0x6081

N_SLAVES = 7


def _reset_slave(client: RobotAdapter, slave: int):
    """复位到 SwitchOnDisabled：Fault 走 FaultReset 沿，其余走 DisableVoltage。"""
    client.set_controlword(slave, 0x00)          # 先清 bit7（保证 FaultReset 上升沿）
    st = client.get_statusword(slave)
    if st.state == "Fault":
        client.set_controlword(slave, CW_FAULT_RESET)  # 0->0x80 上升沿
        st = client.get_statusword(slave)
    if st.state != "SwitchOnDisabled":
        client.set_controlword(slave, CW_DISABLE_VOLTAGE)
        st = client.get_statusword(slave)
    return st


def _enable(client: RobotAdapter, slave: int):
    """标准上电序列：Shutdown -> SwitchOn -> EnableOperation。"""
    _reset_slave(client, slave)
    client.set_controlword(slave, CW_SHUTDOWN)
    client.set_controlword(slave, CW_SWITCH_ON)
    client.set_controlword(slave, CW_ENABLE_OPERATION)
    return client.get_statusword(slave)


# ---------------------------------------------------------------------------
# 1. 上电初始态
# ---------------------------------------------------------------------------
def test_initial_state_all_slaves(client):
    for s in range(N_SLAVES):
        r = client.get_statusword(s)
        assert r.ok and r.state == "SwitchOnDisabled", r
        assert r.statusword == SW_SWITCH_ON_DISABLED, hex(r.statusword)
    capture.DETAILS["test_initial_state_all_slaves"] = (
        f"{N_SLAVES} slaves @ SwitchOnDisabled (0x{SW_SWITCH_ON_DISABLED:04X})")


# ---------------------------------------------------------------------------
# 2. 标准迁移序列（CiA402 6.3.2）
# ---------------------------------------------------------------------------
def test_standard_enable_sequence(client):
    st = _enable(client, 0)
    assert st.state == "OperationEnabled", st
    assert st.statusword == SW_OPERATION_ENABLED, hex(st.statusword)
    capture.DETAILS["test_standard_enable_sequence"] = (
        "Shutdown->ReadyToSwitchOn->SwitchOn->SwitchedOn->EnableOperation "
        "->OperationEnabled (0x0227)")


def test_disable_sequence(client):
    _enable(client, 1)
    st = client.set_controlword(1, CW_SWITCH_ON)      # DisableOperation
    assert st.state == "SwitchedOn", st
    st = client.set_controlword(1, CW_SHUTDOWN)
    assert st.state == "ReadyToSwitchOn", st
    st = client.set_controlword(1, CW_DISABLE_VOLTAGE)
    assert st.state == "SwitchOnDisabled", st
    capture.DETAILS["test_disable_sequence"] = (
        "OperationEnabled->SwitchedOn->ReadyToSwitchOn->SwitchOnDisabled")


# ---------------------------------------------------------------------------
# 3. 非法迁移拒绝（状态保持不变）
# ---------------------------------------------------------------------------
def test_illegal_transition_rejected(client):
    _reset_slave(client, 2)
    # SwitchOnDisabled 直接 EnableOperation：非法，应拒绝
    st = client.set_controlword(2, CW_ENABLE_OPERATION)
    assert st.state == "SwitchOnDisabled", st
    # ReadyToSwitchOn 直接 EnableOperation：非法（需先 SwitchOn）
    client.set_controlword(2, CW_SHUTDOWN)
    st = client.set_controlword(2, CW_ENABLE_OPERATION)
    assert st.state == "ReadyToSwitchOn", st
    capture.DETAILS["test_illegal_transition_rejected"] = (
        "非法命令（0x0F 越级）被拒绝，状态保持")


# ---------------------------------------------------------------------------
# 4. Quick Stop 循环
# ---------------------------------------------------------------------------
def test_quick_stop_cycle(client):
    _enable(client, 3)
    st = client.set_controlword(3, CW_QUICK_STOP)
    assert st.state == "QuickStopActive", st
    st = client.set_controlword(3, CW_ENABLE_OPERATION)
    assert st.state == "OperationEnabled", st
    capture.DETAILS["test_quick_stop_cycle"] = (
        "OperationEnabled->QuickStopActive->OperationEnabled")


# ---------------------------------------------------------------------------
# 5. Fault 注入 + Fault Reset（0x80 上升沿）
# ---------------------------------------------------------------------------
def test_fault_and_fault_reset(client):
    _enable(client, 4)
    st = client.inject_fault(4)
    assert st.state == "Fault", st
    assert st.statusword == SW_FAULT, hex(st.statusword)
    # Fault 下普通命令无效
    st = client.set_controlword(4, CW_ENABLE_OPERATION)
    assert st.state == "Fault", st
    # FaultReset：先清 bit7 再置位（上升沿）
    client.set_controlword(4, 0x00)
    st = client.set_controlword(4, CW_FAULT_RESET)
    assert st.state == "SwitchOnDisabled", st
    capture.DETAILS["test_fault_and_fault_reset"] = (
        "inject_fault->Fault(0x0228)；FaultReset 上升沿 -> SwitchOnDisabled")


def test_fault_reset_requires_edge(client):
    """FaultReset 必须是 0->1 上升沿：连续写 0x80 不应重复复位。"""
    _enable(client, 5)
    client.inject_fault(5)
    client.set_controlword(5, 0x00)   # 清沿
    client.set_controlword(5, CW_FAULT_RESET)  # 沿触发 -> SwitchOnDisabled
    st = client.get_statusword(5)
    assert st.state == "SwitchOnDisabled", st
    client.set_controlword(5, CW_SHUTDOWN)
    client.set_controlword(5, CW_SWITCH_ON)
    client.set_controlword(5, CW_ENABLE_OPERATION)
    client.inject_fault(5)
    assert client.get_statusword(5).state == "Fault"
    # 第二次 0x80 无上升沿（上一次 controlword 已是 0x80 之前？此处从 0x0F 直接跳 0x80 有沿）
    # 先保持 0x80 再写 0x80：无沿，状态不变
    client.set_controlword(5, 0x00)
    client.set_controlword(5, CW_FAULT_RESET)  # 有沿 -> 复位
    assert client.get_statusword(5).state == "SwitchOnDisabled"
    # 再次故障后，写一次 0x80 无沿场景：先写 0x80（沿）后状态已变；再注入故障，
    # 直接写 0x80（上一次 controlword 是 0x80 -> 无沿）不应复位
    client.set_controlword(5, CW_SHUTDOWN)
    client.set_controlword(5, CW_SWITCH_ON)
    client.set_controlword(5, CW_ENABLE_OPERATION)
    client.inject_fault(5)
    assert client.get_statusword(5).state == "Fault"
    client.set_controlword(5, CW_FAULT_RESET)   # 上一次 controlword=0x0F -> 有沿
    client.inject_fault(5)
    client.set_controlword(5, CW_FAULT_RESET)   # 上一次=0x80 -> 无沿，保持 Fault
    st = client.get_statusword(5)
    assert st.state == "Fault", st
    capture.DETAILS["test_fault_reset_requires_edge"] = (
        "连续 0x80 无上升沿时不复位，符合标准语义")


# ---------------------------------------------------------------------------
# 6. 对象字典
# ---------------------------------------------------------------------------
def test_object_dictionary_rw(client):
    _reset_slave(client, 6)
    r = client.write_object(6, OBJ_TARGET_POS, 12345)
    assert r.ok, r
    r = client.read_object(6, OBJ_TARGET_POS)
    assert r.ok and r.value == 12345, r
    r = client.write_object(6, OBJ_PROFILE_VEL, 2000)
    assert r.ok, r
    r = client.read_object(6, OBJ_PROFILE_VEL)
    assert r.ok and r.value == 2000, r
    capture.DETAILS["test_object_dictionary_rw"] = (
        "0x607A=12345 / 0x6081=2000 写入并读回一致")


def test_mode_setting(client):
    _reset_slave(client, 0)
    r = client.set_mode(0, 1)  # Profile Position
    assert r.ok and r.state == "SwitchOnDisabled", r
    r = client.read_object(0, OBJ_MODE)
    assert r.ok and r.value == 1, r
    r = client.read_object(0, OBJ_MODE_DISPLAY)
    assert r.ok and r.value == 1, r
    capture.DETAILS["test_mode_setting"] = "0x6060=1(ProfilePosition)，0x6061 显示一致"


def test_readonly_object_write_rejected(client):
    _reset_slave(client, 1)
    r = client.write_object(1, OBJ_STATUS, 0xFFFF)   # 0x6041 只读
    assert not r.ok, r
    r = client.write_object(1, OBJ_POS_ACTUAL, 999)  # 0x6064 只读
    assert not r.ok, r
    r = client.read_object(1, 0x9999)                # 未知对象
    assert not r.ok, r
    capture.DETAILS["test_readonly_object_write_rejected"] = (
        "写只读对象 0x6041/0x6064 被拒；读未知对象失败")


# ---------------------------------------------------------------------------
# 7. 多从站独立 + 非法从站号
# ---------------------------------------------------------------------------
def test_slave_independence(client):
    _enable(client, 0)
    _reset_slave(client, 1)
    st0 = client.get_statusword(0)
    st1 = client.get_statusword(1)
    assert st0.state == "OperationEnabled", st0
    assert st1.state == "SwitchOnDisabled", st1
    capture.DETAILS["test_slave_independence"] = (
        "slave0=OperationEnabled 而 slave1=SwitchOnDisabled（互不影响）")


def test_invalid_slave_id(client):
    r = client.get_statusword(99)
    assert not r.ok, r
    r = client.set_controlword(99, CW_ENABLE_OPERATION)
    assert not r.ok, r
    capture.DETAILS["test_invalid_slave_id"] = "slave 99 访问返回错误"
