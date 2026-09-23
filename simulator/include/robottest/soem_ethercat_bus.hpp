// SOEM EtherCAT 主站骨架（P2.1a）：真实 HIL 的实现壳。
//
// 当前阶段（P2.1a）目标 = 把"真机接入点"钉死：
//   - 参数校验（iface / slave_count / cycle_hz 非法 -> 构造失败并给出原因）；
//   - 环境探测（Linux: 尝试以 raw socket 打开 iface；Windows: 明确不支持，
//     EtherCAT raw frame 需 Linux 内核支持）；
//   - 未连接 / 无从站时 exchange/run_cycles 返回清晰错误，不挂死、不假装成功。
//
// P2.1b（真机阶段）在 init() 处接入 SOEM（Simple Open EtherCAT Master）：
//   ecx_init(iface) -> 扫描从站 -> 映射 PDO -> 周期 ecx_send/ecx_receive，
//   CycleStats 由真实 WKC / 调度抖动填充——上层 gRPC 与用例零改动。
#pragma once

#include <string>
#include <vector>

#include "robottest/ethercat_master.hpp"
#include "robottest/ethercat_types.hpp"

namespace robottest {

class SoemEthercatBus : public EthercatMaster {
public:
    // 构造即校验；非法参数抛 std::invalid_argument（原因见 err 文本）。
    SoemEthercatBus(std::string iface, int slave_count, int cycle_hz);

    // 探测并初始化网卡/从站。失败时返回 false 并填充 err（清晰原因）。
    // P2.1b：此处替换为 SOEM ecx_init + 从站扫描。
    bool init(std::string& err);

    EthercatBusMode mode() const override { return EthercatBusMode::kHil; }
    int slave_count() const override { return slave_count_; }
    int cycle_hz() const override { return cycle_hz_; }

    bool available() const { return initialized_; }

    // 未 init 成功（或尚未实现）时一律返回 false + 明确错误。
    bool exchange(const std::vector<PdoOutput>& outputs,
                  std::vector<PdoInput>& inputs, std::string& err) override;
    bool run_cycles(const std::vector<PdoOutput>& outputs, int cycles,
                    int cycle_hz, CycleStats& stats,
                    std::vector<PdoInput>& first_inputs,
                    std::vector<PdoInput>& last_inputs,
                    std::string& err) override;

    bool inject_fault(BusFault type, int slave_id) override;
    void clear_fault() override;
    bool faulted() const override;
    BusFault fault_type() const override;
    int fault_slave() const override;

private:
    std::string iface_;
    int slave_count_ = 0;
    int cycle_hz_ = 0;
    bool initialized_ = false;
    std::string last_init_error_;  // 初始化失败原因（exchange 报错时透传）
    BusFault fault_type_ = BusFault::kNone;
    int fault_slave_ = -1;
};

}  // namespace robottest
