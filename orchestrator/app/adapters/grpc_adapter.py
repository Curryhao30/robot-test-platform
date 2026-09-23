"""gRPC Adapter：把 ControllerClient 包装为 RobotAdapter。

Simulation 与 HIL 共用同一实现——区别只在 ControllerClient 指向谁
（本地拉起 vs 远程 HIL 盒子）。用例层不可见差异。
"""
from __future__ import annotations

from app.adapters.base import RobotAdapter


class GrpcAdapter(RobotAdapter):
    def __init__(self, client, profile):
        self._client = client
        self.profile = profile

    # -- 暴露底层（仅 conftest / 报告需要）----------------------------------
    @property
    def client(self):
        return self._client

    @property
    def target(self) -> str:
        return getattr(self._client, "target", "")

    # -- PLCopen ------------------------------------------------------------
    def enable(self, timeout: float = 15.0):
        return self._client.enable(timeout=timeout)

    def disable(self, timeout: float = 15.0):
        return self._client.disable(timeout=timeout)

    def home(self, velocity: float = 60.0, acceleration: float = 150.0,
             deceleration: float = 150.0, timeout: float = 15.0):
        return self._client.home(velocity=velocity, acceleration=acceleration,
                                 deceleration=deceleration, timeout=timeout)

    def move_absolute(self, target_position: list[float], velocity: float = 60.0,
                      acceleration: float = 150.0, deceleration: float = 150.0,
                      jerk: float = 1000.0, abort_current: bool = False,
                      acc: float | None = None, dec: float | None = None):
        return self._client.move_absolute(
            target_position, velocity=velocity, acceleration=acceleration,
            deceleration=deceleration, jerk=jerk, abort_current=abort_current,
            acc=acc, dec=dec)

    def move_relative(self, delta: list[float], velocity: float = 60.0,
                      acceleration: float = 150.0, deceleration: float = 150.0,
                      jerk: float = 1000.0, timeout: float = 15.0):
        return self._client.move_relative(
            delta, velocity=velocity, acceleration=acceleration,
            deceleration=deceleration, jerk=jerk, timeout=timeout)

    def stop(self, emergency: bool = False, timeout: float = 15.0):
        return self._client.stop(emergency=emergency, timeout=timeout)

    def reset(self, timeout: float = 15.0):
        return self._client.reset(timeout=timeout)

    def get_state(self, timeout: float = 15.0):
        return self._client.get_state(timeout=timeout)

    # -- CiA402 ------------------------------------------------------------
    def set_controlword(self, slave_id: int, controlword: int,
                        timeout: float = 15.0):
        return self._client.set_controlword(slave_id, controlword, timeout=timeout)

    def get_statusword(self, slave_id: int, timeout: float = 15.0):
        return self._client.get_statusword(slave_id, timeout=timeout)

    def read_object(self, slave_id: int, index: int, subindex: int = 0,
                    timeout: float = 15.0):
        return self._client.read_object(slave_id, index, subindex=subindex,
                                        timeout=timeout)

    def write_object(self, slave_id: int, index: int, value: int,
                     subindex: int = 0, timeout: float = 15.0):
        return self._client.write_object(slave_id, index, value, subindex=subindex,
                                         timeout=timeout)

    def set_mode(self, slave_id: int, mode: int, timeout: float = 15.0):
        return self._client.set_mode(slave_id, mode, timeout=timeout)

    def inject_fault(self, slave_id: int, timeout: float = 15.0):
        return self._client.inject_fault(slave_id, timeout=timeout)

    # -- EtherCAT -----------------------------------------------------------
    def bus_info(self, timeout: float = 15.0):
        return self._client.bus_info(timeout=timeout)

    def cycle_exchange(self, outputs: list[dict], timeout: float = 15.0):
        return self._client.cycle_exchange(outputs, timeout=timeout)

    def run_cycles(self, outputs: list[dict], cycles: int, cycle_hz: int,
                   timeout: float = 60.0):
        return self._client.run_cycles(outputs, cycles, cycle_hz, timeout=timeout)

    def inject_bus_fault(self, fault_type: int, slave_id: int = -1,
                         timeout: float = 15.0):
        return self._client.inject_bus_fault(fault_type, slave_id=slave_id,
                                             timeout=timeout)

    def clear_bus_fault(self, timeout: float = 15.0):
        return self._client.clear_bus_fault(timeout=timeout)

    # -- 生命周期 -----------------------------------------------------------
    def wait_ready(self, timeout_s: float = 15.0) -> bool:
        return self._client.wait_ready(timeout_s=timeout_s)

    def close(self):
        self._client.close()
