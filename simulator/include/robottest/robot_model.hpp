#pragma once
// RobotModel：多轴机器人"被控对象"模型（Plant）。
// P0 为理想多轴运动学（无 DH/FK、无动力学）；FK/IK/TCP 在后续阶段加入。
// 职责：保存各轴当前状态、限位/限速配置，并提供状态写入。
#include <cstdint>
#include <string>
#include <vector>

namespace robottest {

struct JointLimits {
    double min_pos = -175.0;
    double max_pos = 175.0;
    double max_vel = 120.0;   // deg/s
    double max_acc = 300.0;   // deg/s^2
    double max_jerk = 1000.0; // deg/s^3（P0 未参与剖面计算，预留）
};

struct JointStateT {
    double position = 0.0;     // deg
    double velocity = 0.0;     // deg/s
    double acceleration = 0.0; // deg/s^2
};

struct RobotStateT {
    uint64_t timestamp_ns = 0;
    std::vector<JointStateT> joints;
    bool enabled = false;
    bool moving = false;
    bool error = false;
    int32_t error_code = 0;
    std::string error_message;
    std::string motion_state = "IDLE";  // IDLE/BUSY/ACTIVE/DONE/ABORTED/ERROR
};

class RobotModel {
public:
    explicit RobotModel(std::vector<JointLimits> limits)
        : limits_(std::move(limits)), state_(limits_.size()) {}

    int dof() const { return static_cast<int>(limits_.size()); }
    const std::vector<JointLimits>& limits() const { return limits_; }
    const std::vector<JointStateT>& state() const { return state_; }

    void set_all_state(const std::vector<JointStateT>& s) { state_ = s; }

    /// 目标位置是否全部在限位内
    bool within_position_limits(const std::vector<double>& q) const {
        if (q.size() != limits_.size()) return false;
        for (size_t i = 0; i < q.size(); ++i) {
            if (q[i] < limits_[i].min_pos || q[i] > limits_[i].max_pos) {
                return false;
            }
        }
        return true;
    }

    /// 速度钳制：请求速度 > 轴限速 -> 取限速（控制器行为，供 Velocity Oracle 验证）
    std::vector<double> clamp_velocities(const std::vector<double>& v) const {
        std::vector<double> out(v.size());
        for (size_t i = 0; i < v.size(); ++i) {
            out[i] = std::min(std::max(v[i], 0.0), limits_[i].max_vel);
        }
        return out;
    }

private:
    std::vector<JointLimits> limits_;
    std::vector<JointStateT> state_;
};

}  // namespace robottest
