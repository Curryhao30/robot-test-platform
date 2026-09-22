#pragma once
// TrajectoryGenerator：梯形速度剖面（同步 PTP）。
// 设计：各轴按自身限速/加减速计算最小时长，再按最慢轴同步缩放速度，
// 使所有轴同时起停（PTP 同步运动）。
#include <vector>

#include "robottest/robot_model.hpp"

namespace robottest {

struct TrajectoryPlan {
    bool valid = false;
    double duration_s = 0.0;
    std::vector<double> q0;      // 起始位置
    std::vector<double> q1;      // 目标位置
    std::vector<double> v_peak;  // 同步缩放后的峰值速度
    std::vector<double> a_peak;  // 加速段加速度
    std::vector<double> d_peak;  // 减速段减速度
    std::vector<double> t_acc;   // 加速时长
    std::vector<double> t_cruise;// 匀速时长
    std::vector<double> t_dec;   // 减速时长
};

class TrajectoryGenerator {
public:
    /// 同步 PTP 规划；req_vel/req_acc/req_dec 为请求值（会被限位钳制）
    static TrajectoryPlan plan_synchronized(
        const std::vector<double>& q0,
        const std::vector<double>& q1,
        const std::vector<double>& req_vel,
        const std::vector<double>& req_acc,
        const std::vector<double>& req_dec,
        const std::vector<JointLimits>& limits);

    /// 在时刻 t 求值：输出各轴 pos/vel/acc（deg, deg/s, deg/s^2）
    static void eval(const TrajectoryPlan& p, double t,
                     std::vector<double>& pos,
                     std::vector<double>& vel,
                     std::vector<double>& acc);
};

}  // namespace robottest
