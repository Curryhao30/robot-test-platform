#pragma once
// MotionExecutor：运动执行器。持有当前运动规划，按控制周期推进并输出目标状态。
// 由 ControllerAgent 的控制环线程每 tick 调用 advance()。
#include "robottest/robot_model.hpp"
#include "robottest/trajectory.hpp"

namespace robottest {

class MotionExecutor {
public:
    void start(TrajectoryPlan plan) {
        plan_ = std::move(plan);
        t_ = 0.0;
        active_ = plan_.valid;
    }

    /// 推进 dt 秒；out 输出该时刻各轴目标状态（P0 理想被控对象，指令=实际）
    void advance(double dt, std::vector<JointStateT>& out) {
        if (!active_) return;
        t_ += dt;
        if (t_ >= plan_.duration_s) {
            t_ = plan_.duration_s;
            active_ = false;  // 到达终点
        }
        std::vector<double> pos, vel, acc;
        TrajectoryGenerator::eval(plan_, t_, pos, vel, acc);
        out.resize(pos.size());
        for (size_t i = 0; i < pos.size(); ++i) {
            out[i].position = pos[i];
            out[i].velocity = vel[i];
            out[i].acceleration = acc[i];
        }
    }

    /// 立即中止：仅标记停止；调用方负责将模型速度/加速度清零、位置保持
    void abort() { active_ = false; }

    bool active() const { return active_; }
    double time() const { return t_; }
    double duration() const { return plan_.duration_s; }

private:
    TrajectoryPlan plan_;
    double t_ = 0.0;
    bool active_ = false;
};

}  // namespace robottest
