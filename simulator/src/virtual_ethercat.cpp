#include "robottest/virtual_ethercat.hpp"

#include <algorithm>
#include <chrono>
#include <cmath>
#include <thread>

#include "robottest/object_dictionary.hpp"
#include "robottest/virtual_drive.hpp"

namespace robottest {

namespace {

constexpr int64_t kNanosecondsPerSecond = 1000000000LL;

int64_t now_ns() {
    using namespace std::chrono;
    return duration_cast<nanoseconds>(
               steady_clock::now().time_since_epoch()).count();
}

// 自旋等待到 deadline：避免 sleep 粒度（Windows ~15.6ms / Linux ~1ms）
// 污染周期统计，使 jitter 反映真实调度抖动而非定时器粒度。
void spin_until(int64_t deadline_ns) {
    while (now_ns() < deadline_ns) {
    }
}

}  // namespace

VirtualEthercatBus::VirtualEthercatBus(std::vector<VirtualDrive>& drives,
                                       int cycle_hz)
    : drives_(drives), cycle_hz_(cycle_hz) {}

bool VirtualEthercatBus::exchange(const std::vector<PdoOutput>& outputs,
                                  std::vector<PdoInput>& inputs) {
    inputs.clear();
    for (const auto& o : outputs) {
        if (o.slave_id < 0 || o.slave_id >= static_cast<int>(drives_.size()))
            return false;
        auto& d = drives_[static_cast<size_t>(o.slave_id)];
        d.set_controlword(o.controlword);
        if (o.write_target) {
            std::string err;
            if (!d.write_object(Obj::kTargetPosition,
                                static_cast<uint32_t>(o.target_position), err))
                return false;
        }
        PdoInput in;
        in.slave_id = o.slave_id;
        in.statusword = d.statusword();
        uint32_t pos = 0;
        d.read_object(Obj::kPositionActual, pos);
        in.actual_position = static_cast<int32_t>(pos);
        in.state = d.state_name();
        inputs.push_back(in);
    }
    return true;
}

bool VirtualEthercatBus::run_cycles(const std::vector<PdoOutput>& outputs,
                                    int cycles, int cycle_hz,
                                    CycleStats& stats,
                                    std::vector<PdoInput>& first_inputs,
                                    std::vector<PdoInput>& last_inputs) {
    if (cycles <= 0 || cycle_hz <= 0) return false;
    const int64_t interval_ns = kNanosecondsPerSecond / cycle_hz;
    first_inputs.clear();
    last_inputs.clear();

    std::vector<int64_t> wakes;
    wakes.reserve(static_cast<size_t>(cycles));

    int64_t deadline = now_ns();
    for (int i = 0; i < cycles; ++i) {
        const int64_t t0 = now_ns();
        deadline += interval_ns;
        std::vector<PdoInput> inputs;
        if (!exchange(outputs, inputs)) return false;
        if (i == 0) first_inputs = std::move(inputs);
        last_inputs = inputs;
        wakes.push_back(t0);
        spin_until(deadline);
    }

    // 周期间隔 = 相邻 wake 差；第一周期用名义间隔作为基线。
    std::vector<double> jitter_us;
    jitter_us.reserve(wakes.size());
    double sum_ns = 0.0;
    int64_t prev = wakes[0];
    for (size_t i = 1; i < wakes.size(); ++i) {
        const int64_t actual_ns = wakes[i] - prev;
        prev = wakes[i];
        sum_ns += static_cast<double>(actual_ns);
        jitter_us.push_back(static_cast<double>(actual_ns - interval_ns) / 1000.0);
    }
    if (jitter_us.empty()) {  // 单周期无间隔可统计
        stats.actual_hz = static_cast<double>(cycle_hz);
        stats.jitter_min_us = stats.jitter_max_us = stats.jitter_mean_us =
            stats.jitter_std_us = 0.0;
        return true;
    }

    const double mean_interval_ns = sum_ns / static_cast<double>(jitter_us.size());
    stats.actual_hz = kNanosecondsPerSecond / mean_interval_ns;

    double jsum = 0.0, jsum2 = 0.0;
    double jmin = jitter_us[0], jmax = jitter_us[0];
    for (double j : jitter_us) {
        jmin = std::min(jmin, j);
        jmax = std::max(jmax, j);
        jsum += j;
        jsum2 += j * j;
    }
    const size_t n = jitter_us.size();
    stats.jitter_min_us = jmin;
    stats.jitter_max_us = jmax;
    stats.jitter_mean_us = jsum / static_cast<double>(n);
    const double var = std::max(0.0, jsum2 / static_cast<double>(n) -
                                         stats.jitter_mean_us * stats.jitter_mean_us);
    stats.jitter_std_us = std::sqrt(var);
    return true;
}

}  // namespace robottest
