// 安全联锁状态机（P2.2a，仿真层）：安全输入 -> 联锁输出。
//
// 联锁矩阵（优先级 ESTOP > 门 > 驱动器故障）：
//   ESTOP                     -> ESTOP:   禁止使能 + 停机 + 请求抱闸
//   门开（无 ESTOP）          -> GUARDED: 禁止使能 + 停机 + 请求抱闸
//   驱动器故障（无 ESTOP/门） -> FAULT:   禁止使能 + 停机 + 请求抱闸
//   抱闸未释放（其余正常）     -> SAFE:    禁止使能（防坠落），不要求停机
//   全部正常                  -> SAFE:    允许使能
//
// 边界声明：本模块只验证"安全逻辑与状态联锁"语义；真实安全 I/O（FSoE /
// 硬接线）在 P2.2b HIL 阶段验证，任何安全等级结论不得由仿真层做出。
#pragma once

#include <string>

namespace robottest {

struct SafetyInputs {
    bool estop = false;         // 急停按下
    bool door_open = false;     // 安全门打开
    bool brake_released = false;  // 抱闸释放到位
    bool drive_fault = false;   // 任一驱动器故障
};

struct SafetyOutputs {
    bool allow_enable = true;   // 允许使能
    bool stop_required = false; // 需要停机
    bool brake_request = false; // 请求抱闸
    std::string state = "SAFE"; // SAFE / ESTOP / GUARDED / FAULT
    int error_code = 0;         // 0=OK 1=ESTOP 2=GUARD 3=DRIVE_FAULT
};

class SafetyStateMachine {
public:
    // 以给定输入重新求值，返回联锁输出。
    SafetyOutputs evaluate(const SafetyInputs& in);

    void set_inputs(const SafetyInputs& in) { inputs_ = in; }
    const SafetyInputs& inputs() const { return inputs_; }
    const SafetyOutputs& outputs() const { return outputs_; }

private:
    SafetyInputs inputs_;
    SafetyOutputs outputs_;
};

}  // namespace robottest
