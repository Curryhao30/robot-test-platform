"""Robot Adapter 抽象（P2.0）：用例层唯一依赖。

职责：把"被控对象怎么连"从用例层剥离——
  - simulation：本地拉起 C++ Agent（gRPC）驱动运动仿真/虚拟从站/虚拟总线；
  - hil：连接真实 EtherCAT HIL 盒子（同一套 gRPC 接口，用例零改动）。

用例文件只 import 本层与具体 adapter 的抽象；ControllerClient 只允许
出现在 conftest 与 grpc_adapter 内部。
"""
from __future__ import annotations

from abc import ABC, abstractmethod


class RobotAdapter(ABC):
    """用例层可见的控制器/总线测试接口（与 ControllerClient 语义一致）。

    方法签名刻意与 ControllerClient 对齐，保证 P0/P1 用例零改动切换。
    """

    # -- PLCopen 指令 ------------------------------------------------------
    @abstractmethod
    def enable(self, timeout: float = 15.0): ...

    @abstractmethod
    def disable(self, timeout: float = 15.0): ...

    @abstractmethod
    def home(self, velocity: float = 60.0, acceleration: float = 150.0,
             deceleration: float = 150.0, timeout: float = 15.0): ...

    @abstractmethod
    def move_absolute(self, target_position: list[float], velocity: float = 60.0,
                      acceleration: float = 150.0, deceleration: float = 150.0,
                      jerk: float = 1000.0, abort_current: bool = False,
                      acc: float | None = None, dec: float | None = None): ...

    @abstractmethod
    def move_relative(self, delta: list[float], velocity: float = 60.0,
                      acceleration: float = 150.0, deceleration: float = 150.0,
                      jerk: float = 1000.0, timeout: float = 15.0): ...

    @abstractmethod
    def stop(self, emergency: bool = False, timeout: float = 15.0): ...

    @abstractmethod
    def reset(self, timeout: float = 15.0): ...

    @abstractmethod
    def get_state(self, timeout: float = 15.0): ...

    # -- CiA402 从站 --------------------------------------------------------
    @abstractmethod
    def set_controlword(self, slave_id: int, controlword: int,
                        timeout: float = 15.0): ...

    @abstractmethod
    def get_statusword(self, slave_id: int, timeout: float = 15.0): ...

    @abstractmethod
    def read_object(self, slave_id: int, index: int, subindex: int = 0,
                    timeout: float = 15.0): ...

    @abstractmethod
    def write_object(self, slave_id: int, index: int, value: int,
                     subindex: int = 0, timeout: float = 15.0): ...

    @abstractmethod
    def set_mode(self, slave_id: int, mode: int, timeout: float = 15.0): ...

    @abstractmethod
    def inject_fault(self, slave_id: int, timeout: float = 15.0): ...

    # -- EtherCAT 总线 ------------------------------------------------------
    @abstractmethod
    def bus_info(self, timeout: float = 15.0): ...

    @abstractmethod
    def cycle_exchange(self, outputs: list[dict], timeout: float = 15.0): ...

    @abstractmethod
    def run_cycles(self, outputs: list[dict], cycles: int, cycle_hz: int,
                   timeout: float = 60.0): ...

    @abstractmethod
    def inject_bus_fault(self, fault_type: int, slave_id: int = -1,
                         timeout: float = 15.0): ...

    @abstractmethod
    def clear_bus_fault(self, timeout: float = 15.0): ...

    # -- 安全联锁（P2.2a）---------------------------------------------------
    @abstractmethod
    def set_safety_input(self, *, estop: bool = False, door_open: bool = False,
                         brake_released: bool = True, drive_fault: bool = False,
                         timeout: float = 15.0): ...

    @abstractmethod
    def get_safety_state(self, timeout: float = 15.0): ...

    # -- 生命周期 -----------------------------------------------------------
    @abstractmethod
    def wait_ready(self, timeout_s: float = 15.0) -> bool: ...

    @abstractmethod
    def close(self): ...
