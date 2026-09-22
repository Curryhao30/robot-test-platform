// 虚拟 EtherCAT 总线（P1-2）：主站侧周期过程数据交换（PDO）。
// 纯软件层：Output PDO 写（0x6040 Controlword / 0x607A TargetPos），
// Input PDO 读（0x6041 Statusword / 0x6064 ActualPos + 状态机名）。
// 真实硬件替换点：P2 将本类后端换成 SOEM/IgH 真实主站，gRPC 接口不变。
#pragma once

#include <cstdint>
#include <string>
#include <vector>

namespace robottest {

class VirtualDrive;

// 命名 Pdo* 避免与 proto 生成的 SlaveInput/SlaveOutput 消息冲突
struct PdoOutput {
    int slave_id = 0;
    uint16_t controlword = 0;    // 0x6040 映射
    bool write_target = false;   // 是否写入 0x607A
    int32_t target_position = 0; // 0x607A 映射
};

struct PdoInput {
    int slave_id = 0;
    uint16_t statusword = 0;     // 0x6041 映射
    int32_t actual_position = 0; // 0x6064 映射
    std::string state;           // 状态机状态名
};

struct CycleStats {
    double actual_hz = 0.0;      // 实测平均频率
    double jitter_min_us = 0.0;  // 周期抖动（us）
    double jitter_max_us = 0.0;
    double jitter_mean_us = 0.0;
    double jitter_std_us = 0.0;
    // 实时性（P1-3）：单周期交换处理耗时
    double latency_min_us = 0.0;
    double latency_max_us = 0.0;
    double latency_mean_us = 0.0;
    double latency_std_us = 0.0;
    int overrun_cycles = 0;      // 处理耗时 > 周期间隔的周期数
};

class VirtualEthercatBus {
public:
    VirtualEthercatBus(std::vector<VirtualDrive>& drives, int cycle_hz);

    int slave_count() const { return static_cast<int>(drives_.size()); }
    int cycle_hz() const { return cycle_hz_; }

    // 单周期过程数据交换：写 Output PDO，读 Input PDO。
    // 返回 false 表示 outputs 中含非法从站号。
    bool exchange(const std::vector<PdoOutput>& outputs,
                  std::vector<PdoInput>& inputs);

    // 连续 N 周期（真实计时 + 自旋等待），统计实际频率与周期抖动。
    // 每次交换写同一组 outputs；first/last inputs 供断言。
    bool run_cycles(const std::vector<PdoOutput>& outputs, int cycles,
                    int cycle_hz, CycleStats& stats,
                    std::vector<PdoInput>& first_inputs,
                    std::vector<PdoInput>& last_inputs);

private:
    std::vector<VirtualDrive>& drives_;
    int cycle_hz_;
};

}  // namespace robottest
