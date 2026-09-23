// 虚拟 EtherCAT 总线（P1-2）：主站侧周期过程数据交换（PDO）。
// 纯软件层：Output PDO 写（0x6040 Controlword / 0x607A TargetPos），
// Input PDO 读（0x6041 Statusword / 0x6064 ActualPos + 状态机名）。
//
// P2.1a：继承 EthercatMaster 抽象，作为仿真实现（mode=Simulation）；
// 真机替换点 SoemEthercatBus 提供同一接口，main.cpp 以 --bus 切换。
#pragma once

#include <string>
#include <vector>

#include "robottest/ethercat_master.hpp"
#include "robottest/ethercat_types.hpp"

namespace robottest {

class VirtualDrive;

class VirtualEthercatBus : public EthercatMaster {
public:
    VirtualEthercatBus(std::vector<VirtualDrive>& drives, int cycle_hz);

    EthercatBusMode mode() const override { return EthercatBusMode::kSimulation; }

    int slave_count() const override { return static_cast<int>(drives_.size()); }
    int cycle_hz() const override { return cycle_hz_; }

    // 单周期过程数据交换：写 Output PDO，读 Input PDO。
    // 返回 false 且 err 非空表示交换失败（非法从站 / LINK_LOSS / BUS_ERROR）。
    bool exchange(const std::vector<PdoOutput>& outputs,
                  std::vector<PdoInput>& inputs, std::string& err) override;

    // 连续 N 周期（真实计时 + 自旋等待），统计实际频率与周期抖动。
    // 每次交换写同一组 outputs；first/last inputs 供断言。
    bool run_cycles(const std::vector<PdoOutput>& outputs, int cycles,
                    int cycle_hz, CycleStats& stats,
                    std::vector<PdoInput>& first_inputs,
                    std::vector<PdoInput>& last_inputs,
                    std::string& err) override;

    // 异常注入（P1-5）
    bool inject_fault(BusFault type, int slave_id) override;
    void clear_fault() override;
    bool faulted() const override { return fault_type_ != BusFault::kNone; }
    BusFault fault_type() const override { return fault_type_; }
    int fault_slave() const override { return fault_slave_; }

private:
    std::vector<VirtualDrive>& drives_;
    int cycle_hz_;
    BusFault fault_type_ = BusFault::kNone;
    int fault_slave_ = -1;
};

}  // namespace robottest
