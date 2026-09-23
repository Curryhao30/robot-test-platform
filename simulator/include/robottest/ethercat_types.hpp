// EtherCAT 公共类型（P2.1a）：被 EthercatMaster 抽象与其实现共用。
// 命名 Pdo* 避免与 proto 生成的 SlaveInput/SlaveOutput 消息冲突。
#pragma once

#include <cstdint>
#include <string>
#include <vector>

namespace robottest {

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
    std::string state;           // 状态机状态名；从站丢失时为 "LOST"
    bool lost = false;           // true: 从站已从总线丢失（无响应）
};

// 总线故障注入（P1-5），与 proto BusFaultType 对齐
enum class BusFault {
    kNone = 0,
    kLinkLoss = 1,   // 通信断开：整条总线不可用
    kSlaveLoss = 2,  // 从站丢失：指定从站无响应（其余正常）
    kBusError = 3,   // 总线错误：帧级错误，交换失败
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
    // 逐周期明细（P1-6 波形）：interval_us 长度 = cycles-1，processing_us = cycles
    std::vector<double> interval_us;
    std::vector<double> processing_us;
};

}  // namespace robottest
