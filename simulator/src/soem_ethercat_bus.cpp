#include "robottest/soem_ethercat_bus.hpp"

#include <stdexcept>
#include <string>
#include <utility>

#if !defined(_WIN32)
#include <arpa/inet.h>
#include <net/if.h>
#include <netpacket/packet.h>
#include <sys/socket.h>
#include <unistd.h>
#endif

namespace robottest {

SoemEthercatBus::SoemEthercatBus(std::string iface, int slave_count,
                                 int cycle_hz)
    : iface_(std::move(iface)), slave_count_(slave_count), cycle_hz_(cycle_hz) {
    if (iface_.empty())
        throw std::invalid_argument("SoemEthercatBus: iface 不能为空");
    if (slave_count_ <= 0)
        throw std::invalid_argument("SoemEthercatBus: slave_count 必须 > 0");
    if (cycle_hz_ <= 0)
        throw std::invalid_argument("SoemEthercatBus: cycle_hz 必须 > 0");
}

bool SoemEthercatBus::init(std::string& err) {
    // P2.1a：环境探测骨架。真机阶段（P2.1b）此处替换为：
    //   SOEM: ecx_init(iface_.c_str()) -> 扫描从站 -> 配置 PDO 映射。
#if defined(_WIN32)
    err = "EtherCAT HIL 需在 Linux 运行（raw socket），当前为 Windows；"
          "请使用 --bus virtual 或部署到 Linux 主机";
    return false;
#else
    // Linux：尝试以 AF_PACKET raw socket 打开 iface（仅探测可达性，
    // 不实际发送 EtherCAT 帧——SOEM 依赖留待 P2.1b 引入）。
    int fd = socket(AF_PACKET, SOCK_RAW, htons(ETH_P_ALL));
    if (fd < 0) {
        err = "EtherCAT 网卡探测失败: 无法创建 raw socket（需要 root/CAP_NET_RAW），"
              "iface=" + iface_;
        return false;
    }
    struct sockaddr_ll addr {};
    addr.sll_family = AF_PACKET;
    addr.sll_protocol = htons(ETH_P_ALL);
    addr.sll_ifindex = if_nametoindex(iface_.c_str());
    if (addr.sll_ifindex == 0) {
        close(fd);
        err = "EtherCAT 网卡不存在或不可用: iface=" + iface_ +
              "（请检查网卡名，如 eth0 / enp3s0）";
        return false;
    }
    if (bind(fd, reinterpret_cast<struct sockaddr*>(&addr), sizeof(addr)) < 0) {
        close(fd);
        err = "EtherCAT 网卡绑定失败: iface=" + iface_;
        return false;
    }
    close(fd);
    // 网卡可达。从站数 / PDO 映射需 SOEM（P2.1b）扫描后才可知，
    // 当前骨架以构造参数为准，但标记未初始化：exchange 将明确报错，
    // 避免"骨架冒充真机已连接"。
    initialized_ = false;
    last_init_error_ = "网卡 " + iface_ + " 可达，但 SOEM 从站扫描尚未实现"
                       "（P2.1b）；当前 HIL 总线不可用于交换";
    err = last_init_error_;
    return false;
#endif
}

bool SoemEthercatBus::exchange(const std::vector<PdoOutput>&,
                               std::vector<PdoInput>& inputs,
                               std::string& err) {
    inputs.clear();
    if (!initialized_) {
        err = "HIL not connected: " +
              (last_init_error_.empty() ? "未调用 init() 或初始化失败" : last_init_error_);
        return false;
    }
    err = "HIL exchange 未实现（P2.1b 接入 SOEM）";
    return false;
}

bool SoemEthercatBus::run_cycles(const std::vector<PdoOutput>&, int, int,
                                 CycleStats&, std::vector<PdoInput>&,
                                 std::vector<PdoInput>&, std::string& err) {
    if (!initialized_) {
        err = "HIL not connected: " +
              (last_init_error_.empty() ? "未调用 init() 或初始化失败" : last_init_error_);
        return false;
    }
    err = "HIL run_cycles 未实现（P2.1b 接入 SOEM）";
    return false;
}

bool SoemEthercatBus::inject_fault(BusFault type, int slave_id) {
    // 真机阶段：WKC 检查 / 断线模拟。骨架阶段拒绝注入（总线不可用）。
    (void)type;
    (void)slave_id;
    return false;
}

void SoemEthercatBus::clear_fault() {
    fault_type_ = BusFault::kNone;
    fault_slave_ = -1;
}

bool SoemEthercatBus::faulted() const {
    return fault_type_ != BusFault::kNone;
}

BusFault SoemEthercatBus::fault_type() const { return fault_type_; }

int SoemEthercatBus::fault_slave() const { return fault_slave_; }

}  // namespace robottest
