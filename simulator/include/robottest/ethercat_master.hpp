// EthercatMaster 抽象（P2.1a）：主站侧 EtherCAT 总线接口。
//
// 设计意图（软硬件解耦）：
//   - gRPC EthercatService / 测试用例只依赖本抽象；
//   - VirtualEthercatBus（仿真，P1-2~P1-5）与 SoemEthercatBus（真机，P2.1b）
//     各自实现，切换只发生在 main.cpp 的 --bus 参数；
//   - 实时性统计（CycleStats）、故障注入（BusFault）语义在真机/仿真一致，
//     真机阶段只是数据来源换成 SOEM 的实际 WKC/看门狗/调度抖动。
#pragma once

#include <string>
#include <vector>

#include "robottest/ethercat_types.hpp"

namespace robottest {

// 总线实现模式（--bus 参数）
enum class EthercatBusMode {
    kSimulation = 0,  // 虚拟总线（VirtualEthercatBus）
    kHil = 1,         // 真实 EtherCAT HIL（SoemEthercatBus，P2.1b）
};

class EthercatMaster {
public:
    virtual ~EthercatMaster() = default;

    // 实现模式（报告 / 调试用）
    virtual EthercatBusMode mode() const = 0;

    virtual int slave_count() const = 0;
    virtual int cycle_hz() const = 0;

    // 单周期过程数据交换：写 Output PDO，读 Input PDO。
    // 返回 false 且 err 非空表示交换失败（非法从站 / 链路故障 / HIL 未连接）。
    virtual bool exchange(const std::vector<PdoOutput>& outputs,
                          std::vector<PdoInput>& inputs, std::string& err) = 0;

    // 连续 N 周期（真实计时 + 自旋等待），统计实际频率与周期抖动。
    virtual bool run_cycles(const std::vector<PdoOutput>& outputs, int cycles,
                            int cycle_hz, CycleStats& stats,
                            std::vector<PdoInput>& first_inputs,
                            std::vector<PdoInput>& last_inputs,
                            std::string& err) = 0;

    // 异常注入（P1-5 / 真机阶段：WKC 检查 / 断线模拟）
    virtual bool inject_fault(BusFault type, int slave_id) = 0;
    virtual void clear_fault() = 0;
    virtual bool faulted() const = 0;
    virtual BusFault fault_type() const = 0;
    virtual int fault_slave() const = 0;
};

}  // namespace robottest
