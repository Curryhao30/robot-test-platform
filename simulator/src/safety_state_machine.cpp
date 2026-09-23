#include "robottest/safety_state_machine.hpp"

namespace robottest {

SafetyOutputs SafetyStateMachine::evaluate(const SafetyInputs& in) {
    SafetyOutputs out;    if (in.estop) {
        // 最高优先级：急停 -> 立即停 + FAULT 联动 + 抱闸
        out.state = "ESTOP";
        out.error_code = 1;
        out.allow_enable = false;
        out.stop_required = true;
        out.brake_request = true;
    } else if (in.door_open) {
        // 安全门打开 -> Guard Stop（正常停止语义，非 FAULT）
        out.state = "GUARDED";
        out.error_code = 2;
        out.allow_enable = false;
        out.stop_required = true;
        out.brake_request = true;
    } else if (in.drive_fault) {
        out.state = "FAULT";
        out.error_code = 3;
        out.allow_enable = false;
        out.stop_required = true;
        out.brake_request = true;
    } else if (!in.brake_released) {
        // 抱闸未释放：禁止使能（防坠落），但不要求停机
        out.state = "SAFE";
        out.error_code = 0;
        out.allow_enable = false;
        out.stop_required = false;
        out.brake_request = false;
    } else {
        out.state = "SAFE";
        out.error_code = 0;
        out.allow_enable = true;
        out.stop_required = false;
        out.brake_request = false;
    }
    outputs_ = out;  // 持久化当前联锁输出
    return out;
}

}  // namespace robottest
