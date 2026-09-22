#include "robottest/cia402_state_machine.hpp"

namespace robottest {

std::string cia402_state_name(Cia402State s) {
    switch (s) {
        case Cia402State::Start: return "Start";
        case Cia402State::NotReadyToSwitchOn: return "NotReadyToSwitchOn";
        case Cia402State::SwitchOnDisabled: return "SwitchOnDisabled";
        case Cia402State::ReadyToSwitchOn: return "ReadyToSwitchOn";
        case Cia402State::SwitchedOn: return "SwitchedOn";
        case Cia402State::OperationEnabled: return "OperationEnabled";
        case Cia402State::QuickStopActive: return "QuickStopActive";
        case Cia402State::FaultReactionActive: return "FaultReactionActive";
        case Cia402State::Fault: return "Fault";
    }
    return "Unknown";
}

Cia402StateMachine::Cia402StateMachine() : state_(Cia402State::SwitchOnDisabled) {}

void Cia402StateMachine::inject_fault() {
    if (state_ == Cia402State::Fault || state_ == Cia402State::FaultReactionActive) {
        return;
    }
    state_ = Cia402State::FaultReactionActive;
    fault_reaction_done_ = false;
}

void Cia402StateMachine::update() {
    if (state_ == Cia402State::FaultReactionActive && !fault_reaction_done_) {
        // 模拟故障反应完成（P1：单周期内完成）
        fault_reaction_done_ = true;
        state_ = Cia402State::Fault;
    }
}

void Cia402StateMachine::set_controlword(uint16_t cw) {
    const bool fault_reset_rising =
        (cw & Cia402Command::kFaultReset) &&
        !(last_controlword_ & Cia402Command::kFaultReset);
    last_controlword_ = cw;

    // 推进故障反应时序
    update();

    if (fault_reset_rising) {
        // 仅 Fault 状态可复位（标准：Fault -> SwitchOnDisabled）
        if (state_ == Cia402State::Fault) {
            state_ = Cia402State::SwitchOnDisabled;
        }
        return;
    }

    const uint16_t cmd = cw & Cia402Command::kCmdMask;
    try_transition(cmd);
}

bool Cia402StateMachine::try_transition(uint16_t cmd) {
    // CiA 402 6.3.2 迁移表
    switch (state_) {
        case Cia402State::SwitchOnDisabled:
            if (cmd == Cia402Command::kShutdown) { state_ = Cia402State::ReadyToSwitchOn; return true; }
            // DisableVoltage(0x00) 自环（保持）
            break;
        case Cia402State::ReadyToSwitchOn:
            if (cmd == Cia402Command::kSwitchOn) { state_ = Cia402State::SwitchedOn; return true; }
            if (cmd == Cia402Command::kShutdown) { return true; }  // 自环
            if (cmd == Cia402Command::kDisableVoltage) { state_ = Cia402State::SwitchOnDisabled; return true; }
            break;
        case Cia402State::SwitchedOn:
            if (cmd == Cia402Command::kEnableOperation) { state_ = Cia402State::OperationEnabled; return true; }
            if (cmd == Cia402Command::kShutdown) { state_ = Cia402State::ReadyToSwitchOn; return true; }
            if (cmd == Cia402Command::kDisableVoltage) { state_ = Cia402State::SwitchOnDisabled; return true; }
            break;
        case Cia402State::OperationEnabled:
            if (cmd == Cia402Command::kDisableVoltage) { state_ = Cia402State::SwitchOnDisabled; return true; }
            if (cmd == Cia402Command::kShutdown) { state_ = Cia402State::ReadyToSwitchOn; return true; }
            // DisableOperation(0x07) == SwitchOn(0x07)：OperationEnabled 下解释为回 SwitchedOn
            if (cmd == Cia402Command::kSwitchOn) { state_ = Cia402State::SwitchedOn; return true; }
            if (cmd == Cia402Command::kQuickStop) { state_ = Cia402State::QuickStopActive; return true; }
            break;
        case Cia402State::QuickStopActive:
            if (cmd == Cia402Command::kEnableOperation) { state_ = Cia402State::OperationEnabled; return true; }
            if (cmd == Cia402Command::kDisableVoltage) { state_ = Cia402State::SwitchOnDisabled; return true; }
            break;
        default:
            break;  // Fault/FaultReaction/NotReady/Start：忽略命令
    }
    return false;
}

uint16_t Cia402StateMachine::statusword() const {
    switch (state_) {
        case Cia402State::NotReadyToSwitchOn: return 0x0000;
        case Cia402State::SwitchOnDisabled: return Cia402Statusword::kSwitchOnDisabled;
        case Cia402State::ReadyToSwitchOn: return Cia402Statusword::kReadyToSwitchOn;
        case Cia402State::SwitchedOn: return Cia402Statusword::kSwitchedOn;
        case Cia402State::OperationEnabled: return Cia402Statusword::kOperationEnabled;
        case Cia402State::QuickStopActive: return Cia402Statusword::kQuickStopActive;
        case Cia402State::FaultReactionActive: return Cia402Statusword::kFaultReactionActive;
        case Cia402State::Fault: return Cia402Statusword::kFault;
        case Cia402State::Start: return 0x0000;
    }
    return 0x0000;
}

}  // namespace robottest
