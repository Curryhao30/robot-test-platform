#include "robottest/trajectory.hpp"

#include <algorithm>
#include <cmath>
#include <limits>

namespace robottest {

namespace {

constexpr double kEps = 1e-9;

// 单轴梯形剖面计算（不缩放）：返回 (v_peak, a, d, t_acc, t_cruise, t_dec)
void single_axis_plan(double dist, double req_v, double req_a, double req_d,
                      const JointLimits& lim,
                      double& v, double& a, double& d,
                      double& t_acc, double& t_cruise, double& t_dec) {
    const double D = std::abs(dist);
    if (D < kEps) {
        v = a = d = t_acc = t_cruise = t_dec = 0.0;
        return;
    }
    v = std::min(std::max(req_v, 0.0), lim.max_vel);
    a = std::min(std::max(req_a, 0.0), lim.max_acc);
    d = std::min(std::max(req_d, 0.0), lim.max_acc);

    const double tri_dist = v * v / (2.0 * a) + v * v / (2.0 * d);
    if (tri_dist >= D) {  // 三角形剖面：达不到 v
        const double denom = a + d;
        v = std::sqrt(2.0 * D * a * d / denom);
        t_acc = v / a;
        t_dec = v / d;
        t_cruise = 0.0;
    } else {  // 梯形剖面
        t_acc = v / a;
        t_dec = v / d;
        t_cruise = (D - 0.5 * v * t_acc - 0.5 * v * t_dec) / v;
    }
}

}  // namespace

TrajectoryPlan TrajectoryGenerator::plan_synchronized(
    const std::vector<double>& q0,
    const std::vector<double>& q1,
    const std::vector<double>& req_vel,
    const std::vector<double>& req_acc,
    const std::vector<double>& req_dec,
    const std::vector<JointLimits>& limits) {
    TrajectoryPlan p;
    const size_t n = q0.size();
    if (n == 0 || q1.size() != n || limits.size() != n) return p;

    p.valid = true;
    p.q0 = q0;
    p.q1 = q1;
    p.v_peak.assign(n, 0.0);
    p.a_peak.assign(n, 0.0);
    p.d_peak.assign(n, 0.0);
    p.t_acc.assign(n, 0.0);
    p.t_cruise.assign(n, 0.0);
    p.t_dec.assign(n, 0.0);

    std::vector<double> raw_v(n), raw_t_acc(n), raw_t_dec(n), raw_t_cruise(n);
    double T = 0.0;
    for (size_t i = 0; i < n; ++i) {
        double v, a, d, t_acc, t_cruise, t_dec;
        single_axis_plan(q1[i] - q0[i], req_vel[i], req_acc[i], req_dec[i],
                         limits[i], v, a, d, t_acc, t_cruise, t_dec);
        raw_v[i] = v;
        raw_t_acc[i] = t_acc;
        raw_t_cruise[i] = t_cruise;
        raw_t_dec[i] = t_dec;
        T = std::max(T, t_acc + t_cruise + t_dec);
    }
    p.duration_s = T;

    // 同步缩放：保持各轴加速/减速时长不变（v 与 a,d 同比例缩放），
    // 由目标时长 T 反解缩放后的峰值速度，使所有轴同时起停。
    for (size_t i = 0; i < n; ++i) {
        const double D = std::abs(q1[i] - q0[i]);
        if (D < kEps) continue;
        const double t_acc = raw_t_acc[i];
        const double t_dec = raw_t_dec[i];
        const double denom = T - 0.5 * (t_acc + t_dec);  // > 0（T >= 轴时长）
        const double v_scaled = D / denom;
        p.v_peak[i] = v_scaled;
        p.a_peak[i] = (t_acc > kEps) ? (v_scaled / t_acc) : 0.0;
        p.d_peak[i] = (t_dec > kEps) ? (v_scaled / t_dec) : 0.0;
        p.t_acc[i] = t_acc;
        p.t_dec[i] = t_dec;
        p.t_cruise[i] = std::max(T - t_acc - t_dec, 0.0);
    }
    return p;
}

void TrajectoryGenerator::eval(const TrajectoryPlan& p, double t,
                               std::vector<double>& pos,
                               std::vector<double>& vel,
                               std::vector<double>& acc) {
    const size_t n = p.q0.size();
    pos.assign(n, 0.0);
    vel.assign(n, 0.0);
    acc.assign(n, 0.0);
    if (!p.valid || n == 0) return;

    for (size_t i = 0; i < n; ++i) {
        const double d = p.q1[i] - p.q0[i];
        const double s = (d >= 0.0) ? 1.0 : -1.0;
        if (std::abs(d) < kEps) {
            pos[i] = p.q0[i];
            continue;
        }
        const double t_acc = p.t_acc[i];
        const double t_cruise = p.t_cruise[i];
        const double t_dec = p.t_dec[i];
        const double t_end = t_acc + t_cruise + t_dec;

        if (t < t_acc) {
            pos[i] = p.q0[i] + s * 0.5 * p.a_peak[i] * t * t;
            vel[i] = s * p.a_peak[i] * t;
            acc[i] = s * p.a_peak[i];
        } else if (t < t_acc + t_cruise) {
            const double t_mid = t - t_acc;
            pos[i] = p.q0[i] + s * (0.5 * p.v_peak[i] * t_acc +
                                    p.v_peak[i] * t_mid);
            vel[i] = s * p.v_peak[i];
            acc[i] = 0.0;
        } else if (t < t_end) {
            const double rem = t_end - t;
            pos[i] = p.q1[i] - s * 0.5 * p.d_peak[i] * rem * rem;
            vel[i] = s * p.d_peak[i] * rem;
            acc[i] = -s * p.d_peak[i];
        } else {
            pos[i] = p.q1[i];
            vel[i] = 0.0;
            acc[i] = 0.0;
        }
    }
}

}  // namespace robottest
