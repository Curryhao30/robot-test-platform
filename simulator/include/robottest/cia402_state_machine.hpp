// CiA 402 驱动器状态机（IEC 61800-7-201）
// 纯软件实现，作为虚拟从站的核心；不依赖任何真实硬件/总线。
// 状态迁移按 CiA 402 文档 6.3.2（状态图 + 迁移命令）实现，
// 非法迁移被拒绝（状态保持不变）。
#pragma once

#include <cstdint>
#include <string>

namespace robottest {

// 标准状态（9 个真实状态 + Start 虚拟态）
enum class Cia402State : int {
  Start = 0,
  NotReadyToSwitchOn = 1,
  SwitchOnDisabled = 2,
  ReadyToSwitchOn = 3,
  SwitchedOn = 4,
  OperationEnabled = 5,
  QuickStopActive = 6,
  FaultReactionActive = 7,
  Fault = 8,
};

std::string cia402_state_name(Cia402State s);

// 标准 Controlword 命令（0x6040 低 4 位；Fault Reset 为 bit7 上升沿）
struct Cia402Command {
  static constexpr uint16_t kDisableVoltage = 0x00;  // 禁用电压
  static constexpr uint16_t kQuickStop = 0x02;       // 快速停止
  static constexpr uint16_t kShutdown = 0x06;        // 关断（进入 ReadyToSwitchOn）
  static constexpr uint16_t kSwitchOn = 0x07;        // 合闸
  static constexpr uint16_t kEnableOperation = 0x0F; // 使能运行
  static constexpr uint16_t kFaultReset = 0x80;      // bit7（需 0->1 沿）
  static constexpr uint16_t kCmdMask = 0x0F;         // 命令位掩码
};

// 标准 Statusword 值（0x6041，含 bit9 Remote=1 表示经现场总线远程控制）
struct Cia402Statusword {
  static constexpr uint16_t kSwitchOnDisabled = 0x0250;
  static constexpr uint16_t kReadyToSwitchOn = 0x0221;
  static constexpr uint16_t kSwitchedOn = 0x0223;
  static constexpr uint16_t kOperationEnabled = 0x0227;
  static constexpr uint16_t kQuickStopActive = 0x0227;
  static constexpr uint16_t kFaultReactionActive = 0x022F;
  static constexpr uint16_t kFault = 0x0228;
};

class Cia402StateMachine {
public:
  Cia402StateMachine();

  // 写 Controlword（0x6040）：解析命令并执行迁移；bit7 记录上升沿。
  void set_controlword(uint16_t cw);

  // 注入故障（测试辅助）：任意状态 -> FaultReactionActive -> Fault。
  // reaction 完成后（下一次 update / 立即）进入 Fault。
  void inject_fault();

  // 推进内部时序（Fault Reaction 完成、Fault 停留等）。
  void update();

  Cia402State state() const { return state_; }
  uint16_t statusword() const;
  std::string state_name() const { return cia402_state_name(state_); }
  bool in_fault() const { return state_ == Cia402State::Fault; }

private:
  void transition(Cia402State next);
  bool try_transition(uint16_t cmd);

  Cia402State state_;
  uint16_t last_controlword_ = 0;
  bool fault_reaction_done_ = true;
  bool fault_reset_pending_ = false;
};

}  // namespace robottest
