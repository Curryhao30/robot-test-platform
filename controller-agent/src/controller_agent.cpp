#include "robottest/controller_agent.hpp"

#include <algorithm>
#include <chrono>
#include <cmath>
#include <limits>

namespace robottest {

namespace {
constexpr double kEps = 1e-9;

std::vector<double> ones(size_t n, double v) {
    return std::vector<double>(n, v);
}

uint64_t now_ns() {
    return static_cast<uint64_t>(
        std::chrono::duration_cast<std::chrono::nanoseconds>(
            std::chrono::steady_clock::now().time_since_epoch())
            .count());
}
}  // namespace

ControllerAgent::ControllerAgent(std::vector<JointLimits> limits,
                                 int cycle_hz, std::string profile_name)
    : limits_(std::move(limits)),
      cycle_hz_(cycle_hz > 0 ? cycle_hz : 1000),
      profile_name_(std::move(profile_name)),
      model_(limits_) {}

// ---------------------------------------------------------------------------
// 命令接口
// ---------------------------------------------------------------------------
CommandOutcome ControllerAgent::enable() {
    std::lock_guard<std::mutex> lk(mu_);
    CommandOutcome r;
    if (ctrl_state_ == CtrlState::kFault) {
        r.ok = false;
        r.error_code = kErrFault;
        r.error_message = "controller in FAULT, call Reset first";
        r.motion_state = kMotionError;
        return r;
    }
    ctrl_state_ = CtrlState::kEnabled;
    enabled_ = true;
    error_code_ = kErrOk;
    error_message_.clear();
    motion_state_ = kMotionIdle;
    r.ok = true;
    r.motion_state = kMotionIdle;
    return r;
}

CommandOutcome ControllerAgent::disable() {
    std::lock_guard<std::mutex> lk(mu_);
    CommandOutcome r;
    if (busy_) {  // 去使能时中止当前运动
        const uint64_t id = current_motion_id_;
        busy_ = false;
        motion_state_ = kMotionAborted;
        finished_ids_.push_back(id);
        cv_.notify_all();
    }
    ctrl_state_ = CtrlState::kDisabled;
    enabled_ = false;
    r.ok = true;
    r.motion_state = kMotionIdle;
    return r;
}

CommandOutcome ControllerAgent::home(const MotionSpec& spec) {
    MotionSpec s = spec;
    s.target.assign(model_.dof(), 0.0);  // P0：回零
    return run_motion(s, false, "MC_Home");
}

CommandOutcome ControllerAgent::move_absolute(const MotionSpec& spec,
                                              bool abort_current) {
    return run_motion(spec, abort_current, "MC_MoveAbsolute");
}

CommandOutcome ControllerAgent::move_relative(const MotionSpec& spec,
                                              bool abort_current) {
    MotionSpec s = spec;
    {
        std::lock_guard<std::mutex> lk(mu_);
        const auto& st = model_.state();
        for (size_t i = 0; i < s.target.size() && i < st.size(); ++i) {
            s.target[i] = st[i].position + s.target[i];
        }
    }
    return run_motion(s, abort_current, "MC_MoveRelative");
}

CommandOutcome ControllerAgent::stop(bool emergency) {
    std::lock_guard<std::mutex> lk(mu_);
    CommandOutcome r;
    if (emergency) {
        emergency_requested_ = true;
        ctrl_state_ = CtrlState::kFault;
        enabled_ = false;
        error_code_ = kErrEmergencyStop;
        error_message_ = "emergency stop";
        motion_state_ = kMotionError;
        r.ok = true;
        r.error_code = kErrEmergencyStop;
        r.error_message = error_message_;
        r.motion_state = kMotionError;
    } else {
        stop_requested_ = true;
        r.ok = true;
        r.motion_state = kMotionIdle;
    }
    cv_.notify_all();
    return r;
}

CommandOutcome ControllerAgent::reset() {
    std::lock_guard<std::mutex> lk(mu_);
    CommandOutcome r;
    if (ctrl_state_ == CtrlState::kFault || error_code_ != kErrOk) {
        ctrl_state_ = CtrlState::kEnabled;
        enabled_ = true;
        error_code_ = kErrOk;
        error_message_.clear();
        motion_state_ = kMotionIdle;
    }
    r.ok = true;
    r.motion_state = kMotionIdle;
    return r;
}

RobotStateT ControllerAgent::get_state() {
    std::lock_guard<std::mutex> lk(mu_);
    RobotStateT s;
    s.timestamp_ns = now_ns();
    s.joints = model_.state();
    s.enabled = enabled_;
    s.moving = busy_;
    s.error = (ctrl_state_ == CtrlState::kFault) || (error_code_ != kErrOk);
    s.error_code = error_code_;
    s.error_message = error_message_;
    s.motion_state = motion_state_;
    return s;
}

// ---------------------------------------------------------------------------
// 运动执行
// ---------------------------------------------------------------------------
CommandOutcome ControllerAgent::run_motion(const MotionSpec& spec,
                                           bool abort_current,
                                           const std::string& cmd) {
    std::unique_lock<std::mutex> lk(mu_);

    // 1) 使能检查
    if (ctrl_state_ != CtrlState::kEnabled) {
        CommandOutcome r;
        r.ok = false;
        r.error_code = kErrNotEnabled;
        r.error_message = "servo not enabled";
        r.motion_state = kMotionError;
        return r;
    }

    // 2) 忙检查：可中止（abort_current）或拒绝（BUSY）
    if (busy_) {
        if (!abort_current) {
            CommandOutcome r;
            r.ok = false;
            r.error_code = kErrBusy;
            r.error_message = "another motion in progress";
            r.motion_state = kMotionError;
            return r;
        }
        pending_abort_ = true;
        cv_.notify_all();
        // 等待控制环中止当前运动
        while (busy_) cv_.wait(lk);
    }

    // 3) 限位校验：目标越界 -> 报错且不得运动
    if (!model_.within_position_limits(spec.target)) {
        motion_state_ = kMotionError;
        error_code_ = kErrJointLimit;
        error_message_ = "target position out of joint limits";
        CommandOutcome r;
        r.ok = false;
        r.error_code = kErrJointLimit;
        r.error_message = error_message_;
        r.motion_state = kMotionError;
        return r;
    }

    // 4) 规划并启动运动
    const auto& st = model_.state();
    std::vector<double> q0(model_.dof()), req_v(model_.dof()),
        req_a(model_.dof()), req_d(model_.dof());
    for (int i = 0; i < model_.dof(); ++i) q0[i] = st[i].position;
    std::fill(req_v.begin(), req_v.end(), spec.velocity);
    std::fill(req_a.begin(), req_a.end(), spec.acceleration);
    std::fill(req_d.begin(), req_d.end(), spec.deceleration);

    TrajectoryPlan plan = TrajectoryGenerator::plan_synchronized(
        q0, spec.target, req_v, req_a, req_d, limits_);
    if (!plan.valid) {
        CommandOutcome r;
        r.ok = false;
        r.error_code = kErrUnknown;
        r.error_message = "trajectory plan failed";
        r.motion_state = kMotionError;
        return r;
    }

    const uint64_t my_id = next_motion_id_++;
    current_motion_id_ = my_id;
    stop_requested_ = false;
    pending_abort_ = false;
    executor_.start(std::move(plan));
    busy_ = true;
    motion_state_ = kMotionBusy;
    error_code_ = kErrOk;
    error_message_.clear();
    recorder_.begin(my_id);

    // 5) 等待完成（控制环负责 DONE / ABORTED / ERROR）
    const auto start = std::chrono::steady_clock::now();
    while (std::find(finished_ids_.begin(), finished_ids_.end(), my_id) ==
           finished_ids_.end()) {
        cv_.wait(lk);
    }
    const auto end = std::chrono::steady_clock::now();

    CommandOutcome r;
    // 终态按 motion_id 记录（控制环写入），避免被并发新命令覆盖全局状态
    auto it = outcomes_.find(my_id);
    if (it != outcomes_.end()) {
        r.ok = it->second.ok;
        r.error_code = it->second.error_code;
        r.error_message = it->second.error_message;
        r.motion_state = it->second.motion_state;
        outcomes_.erase(it);
    } else {
        r.ok = (motion_state_ == kMotionDone);
        r.error_code = error_code_;
        r.error_message = error_message_;
        r.motion_state = motion_state_;
    }
    r.duration_ms = static_cast<uint64_t>(
        std::chrono::duration_cast<std::chrono::milliseconds>(end - start)
            .count());
    r.samples = recorder_.take(my_id);
    return r;
}

// ---------------------------------------------------------------------------
// 控制环（P0：1000Hz；采样与实时执行全部在本线程内完成）
// ---------------------------------------------------------------------------
void ControllerAgent::start_control_loop() {
    running_ = true;
    loop_thread_ = std::thread([this] { control_loop(); });
}

void ControllerAgent::control_loop() {
    const double dt = 1.0 / static_cast<double>(cycle_hz_);
    auto next = std::chrono::steady_clock::now();
    while (running_) {
        next = next + std::chrono::microseconds(
                          static_cast<long long>(dt * 1'000'000.0));
        {
            std::lock_guard<std::mutex> lk(mu_);
            if (busy_) {
                const uint64_t id = current_motion_id_;
                std::vector<JointStateT> next_state;
                if (emergency_requested_) {
                    executor_.abort();
                    next_state = model_.state();
                    for (auto& j : next_state) {
                        j.velocity = 0.0;
                        j.acceleration = 0.0;
                    }
                    model_.set_all_state(next_state);
                    busy_ = false;
                    motion_state_ = kMotionError;
                    finished_ids_.push_back(id);
                    emergency_requested_ = false;
                    outcomes_[id] =
                        CommandOutcome{false, kErrEmergencyStop,
                                       "emergency stop", kMotionError, 0, {}};
                    TrajectorySampleT s;
                    s.timestamp_ns = now_ns();
                    s.joints = next_state;
                    s.motion_state = motion_state_;
                    recorder_.add(id, std::move(s));
                    cv_.notify_all();
                } else if (stop_requested_ || pending_abort_) {
                    executor_.abort();
                    next_state = model_.state();
                    for (auto& j : next_state) {
                        j.velocity = 0.0;
                        j.acceleration = 0.0;
                    }
                    model_.set_all_state(next_state);
                    busy_ = false;
                    motion_state_ = kMotionAborted;
                    error_code_ = kErrAborted;
                    error_message_ = pending_abort_
                                         ? "aborted by new command"
                                         : "aborted by stop";
                    finished_ids_.push_back(id);
                    stop_requested_ = false;
                    pending_abort_ = false;
                    outcomes_[id] =
                        CommandOutcome{false, kErrAborted, error_message_,
                                       kMotionAborted, 0, {}};
                    TrajectorySampleT s;
                    s.timestamp_ns = now_ns();
                    s.joints = next_state;
                    s.motion_state = motion_state_;
                    recorder_.add(id, std::move(s));
                    cv_.notify_all();
                } else {
                    executor_.advance(dt, next_state);
                    model_.set_all_state(next_state);
                    if (!executor_.active()) {
                        busy_ = false;
                        motion_state_ = kMotionDone;
                        finished_ids_.push_back(id);
                        outcomes_[id] =
                            CommandOutcome{true, kErrOk, "", kMotionDone, 0, {}};
                        cv_.notify_all();
                    } else {
                        motion_state_ = kMotionActive;
                    }
                    TrajectorySampleT s;
                    s.timestamp_ns = now_ns();
                    s.joints = next_state;
                    s.motion_state = motion_state_;
                    recorder_.add(id, std::move(s));
                }
            }
        }
        std::this_thread::sleep_until(next);
    }
}

void ControllerAgent::stop_control_loop() {
    running_ = false;
    if (loop_thread_.joinable()) loop_thread_.join();
}

void ControllerAgent::join() {
    if (loop_thread_.joinable()) loop_thread_.join();
}

}  // namespace robottest
