#pragma once
// ControllerAgent：控制器软件核心（P0）。
//
// 职责边界（与 Python Orchestrator 解耦）：
//   - Python 只下发一次命令（Enable/Home/MoveAbsolute/...）；
//   - 控制周期内的高频执行与采样全部在本类内部完成（控制环线程）；
//   - 完成后把 timestamp[]/joint[]/状态 批量返回，避免解释器与 RPC 调度
//     影响实时执行。
//
// 内部结构：
//   ControlLoopThread（1000Hz）-> MotionExecutor -> RobotModel(Simulator)
//   采样 -> DataRecorder -> gRPC 批量返回
#include <atomic>
#include <condition_variable>
#include <cstdint>
#include <map>
#include <memory>
#include <mutex>
#include <string>
#include <thread>
#include <vector>

#include "robottest/motion_executor.hpp"
#include "robottest/robot_model.hpp"

namespace robottest {

// 错误码（与 controller.proto 对齐）
constexpr int32_t kErrOk = 0;
constexpr int32_t kErrNotEnabled = 1;
constexpr int32_t kErrJointLimit = 2;
constexpr int32_t kErrBusy = 3;
constexpr int32_t kErrAborted = 4;
constexpr int32_t kErrEmergencyStop = 5;
constexpr int32_t kErrFault = 6;
constexpr int32_t kErrUnknown = 7;

// PLCopen 运动状态
inline const char* kMotionIdle = "IDLE";
inline const char* kMotionBusy = "BUSY";
inline const char* kMotionActive = "ACTIVE";
inline const char* kMotionDone = "DONE";
inline const char* kMotionAborted = "ABORTED";
inline const char* kMotionError = "ERROR";

enum class CtrlState { kDisabled, kEnabled, kFault };

struct TrajectorySampleT {
    uint64_t timestamp_ns = 0;
    std::vector<JointStateT> joints;
    std::string motion_state;
};

/// 采样缓冲：按 motion_id 隔离，保证"被新命令中止的旧运动"仍可取回自身轨迹
class DataRecorder {
public:
    void begin(uint64_t id) { buffers_[id].clear(); }
    void add(uint64_t id, TrajectorySampleT s) { buffers_[id].push_back(std::move(s)); }
    std::vector<TrajectorySampleT> take(uint64_t id) {
        auto it = buffers_.find(id);
        if (it == buffers_.end()) return {};
        auto out = std::move(it->second);
        buffers_.erase(it);
        return out;
    }

private:
    std::map<uint64_t, std::vector<TrajectorySampleT>> buffers_;
};

/// 命令执行结果（与 proto CommandResult 对应）
struct CommandOutcome {
    bool ok = false;
    int32_t error_code = 0;
    std::string error_message;
    std::string motion_state = kMotionIdle;
    uint64_t duration_ms = 0;
    std::vector<TrajectorySampleT> samples;
};

struct MotionSpec {
    std::vector<double> target;   // 目标位置（deg）
    double velocity = 0.0;        // deg/s
    double acceleration = 0.0;    // deg/s^2
    double deceleration = 0.0;    // deg/s^2
    double jerk = 0.0;            // deg/s^3
};

class ControllerAgent {
public:
    ControllerAgent(std::vector<JointLimits> limits, int cycle_hz,
                    std::string profile_name);

    // -- 控制接口（gRPC 服务层调用） --------------------------------------
    CommandOutcome enable();
    CommandOutcome disable();
    CommandOutcome home(const MotionSpec& spec);
    CommandOutcome move_absolute(const MotionSpec& spec, bool abort_current);
    CommandOutcome move_relative(const MotionSpec& spec, bool abort_current);
    CommandOutcome stop(bool emergency);
    CommandOutcome reset();
    RobotStateT get_state();

    // -- 生命周期 ----------------------------------------------------------
    void start_control_loop();
    void stop_control_loop();
    void join();

    int cycle_hz() const { return cycle_hz_; }
    const std::string& profile_name() const { return profile_name_; }

private:
    // 启动一次同步 PTP 运动并等待完成（限位校验失败返回错误结果）
    CommandOutcome run_motion(const MotionSpec& spec, bool abort_current,
                              const std::string& cmd);
    CommandOutcome make_outcome(const std::string& cmd, uint64_t motion_id);

    void control_loop();

    // -- 状态 --------------------------------------------------------------
    std::vector<JointLimits> limits_;
    int cycle_hz_;
    std::string profile_name_;
    RobotModel model_;
    MotionExecutor executor_;

    std::mutex mu_;
    std::condition_variable cv_;

    CtrlState ctrl_state_ = CtrlState::kDisabled;
    std::string motion_state_ = kMotionIdle;
    int32_t error_code_ = kErrOk;
    std::string error_message_;
    bool enabled_ = false;

    bool busy_ = false;
    uint64_t current_motion_id_ = 0;
    uint64_t next_motion_id_ = 1;
    std::vector<uint64_t> finished_ids_;
    bool stop_requested_ = false;
    bool emergency_requested_ = false;
    bool pending_abort_ = false;  // 新命令要求中止当前运动

    DataRecorder recorder_;
    std::map<uint64_t, CommandOutcome> outcomes_;  // 按 motion_id 的终态
    std::thread loop_thread_;
    std::atomic<bool> running_{false};
};

}  // namespace robottest
