// controller_agent 主程序
// 用法: controller_agent --profile <robot_profiles/maira_sim.yaml> [--port 50051]
#include <csignal>
#include <iostream>
#include <string>
#include <vector>

#include <grpcpp/grpcpp.h>
#include <yaml-cpp/yaml.h>

#include "controller.grpc.pb.h"
#include "controller.pb.h"
#include "robottest/controller_agent.hpp"

namespace {

// ---------------------------------------------------------------------------
// gRPC 服务实现：将 proto 请求桥接到 ControllerAgent
// ---------------------------------------------------------------------------
class ControllerServiceImpl final
    : public robottest::ControllerService::Service {
public:
    explicit ControllerServiceImpl(robottest::ControllerAgent& agent)
        : agent_(agent) {}

    grpc::Status Enable(grpc::ServerContext*,
                        const robottest::EnableRequest*,
                        robottest::CommandResult* out) override {
        fill(agent_.enable(), "Enable", out);
        return grpc::Status::OK;
    }

    grpc::Status Disable(grpc::ServerContext*,
                         const robottest::DisableRequest*,
                         robottest::CommandResult* out) override {
        fill(agent_.disable(), "Disable", out);
        return grpc::Status::OK;
    }

    grpc::Status Home(grpc::ServerContext*,
                      const robottest::HomeRequest* req,
                      robottest::CommandResult* out) override {
        robottest::MotionSpec spec;
        spec.velocity = req->velocity();
        spec.acceleration = req->acceleration();
        spec.deceleration = req->deceleration();
        fill(agent_.home(spec), "MC_Home", out);
        return grpc::Status::OK;
    }

    grpc::Status MoveAbsolute(grpc::ServerContext*,
                              const robottest::MoveAbsoluteRequest* req,
                              robottest::CommandResult* out) override {
        robottest::MotionSpec spec;
        spec.target.assign(req->target_position().begin(),
                           req->target_position().end());
        spec.velocity = req->velocity();
        spec.acceleration = req->acceleration();
        spec.deceleration = req->deceleration();
        spec.jerk = req->jerk();
        fill(agent_.move_absolute(spec, req->abort_current()),
             "MC_MoveAbsolute", out);
        return grpc::Status::OK;
    }

    grpc::Status MoveRelative(grpc::ServerContext*,
                              const robottest::MoveRelativeRequest* req,
                              robottest::CommandResult* out) override {
        robottest::MotionSpec spec;
        spec.target.assign(req->delta().begin(), req->delta().end());
        spec.velocity = req->velocity();
        spec.acceleration = req->acceleration();
        spec.deceleration = req->deceleration();
        spec.jerk = req->jerk();
        fill(agent_.move_relative(spec, false), "MC_MoveRelative", out);
        return grpc::Status::OK;
    }

    grpc::Status Stop(grpc::ServerContext*,
                      const robottest::StopRequest* req,
                      robottest::CommandResult* out) override {
        fill(agent_.stop(req->emergency()), "Stop", out);
        return grpc::Status::OK;
    }

    grpc::Status Reset(grpc::ServerContext*,
                       const robottest::ResetRequest*,
                       robottest::CommandResult* out) override {
        fill(agent_.reset(), "Reset", out);
        return grpc::Status::OK;
    }

    grpc::Status GetState(grpc::ServerContext*,
                          const robottest::GetStateRequest*,
                          robottest::RobotState* out) override {
        const auto& st = agent_.get_state();
        out->set_timestamp_ns(st.timestamp_ns);
        out->set_enabled(st.enabled);
        out->set_moving(st.moving);
        out->set_error(st.error);
        out->set_error_code(st.error_code);
        out->set_error_message(st.error_message);
        out->set_motion_state(st.motion_state);
        for (const auto& j : st.joints) {
            auto* js = out->add_joints();
            js->set_position(j.position);
            js->set_velocity(j.velocity);
            js->set_acceleration(j.acceleration);
        }
        return grpc::Status::OK;
    }

private:
    static void fill(const robottest::CommandOutcome& r,
                     const std::string& cmd, robottest::CommandResult* out) {
        out->set_ok(r.ok);
        out->set_error_code(r.error_code);
        out->set_error_message(r.error_message);
        out->set_command(cmd);
        out->set_motion_state(r.motion_state);
        out->set_duration_ms(r.duration_ms);
        out->set_sample_count(static_cast<int32_t>(r.samples.size()));
        for (const auto& s : r.samples) {
            auto* p = out->add_samples();
            p->set_timestamp_ns(s.timestamp_ns);
            p->set_motion_state(s.motion_state);
            for (const auto& j : s.joints) {
                auto* js = p->add_joints();
                js->set_position(j.position);
                js->set_velocity(j.velocity);
                js->set_acceleration(j.acceleration);
            }
        }
    }

    robottest::ControllerAgent& agent_;
};

// ---------------------------------------------------------------------------
// Profile 加载（与 orchestrator 读取同一份 YAML）
// ---------------------------------------------------------------------------
bool load_profile(const std::string& path,
                  std::vector<robottest::JointLimits>& limits,
                  int& cycle_hz, std::string& name) {
    try {
        YAML::Node root = YAML::LoadFile(path);
        name = root["name"].as<std::string>("profile");
        cycle_hz = root["controller"]["cycle_hz"].as<int>(1000);
        for (const auto& j : root["joints"]) {
            const auto& lim = j["limits"];
            robottest::JointLimits l;
            l.min_pos = lim["position"][0].as<double>();
            l.max_pos = lim["position"][1].as<double>();
            l.max_vel = lim["velocity"].as<double>();
            l.max_acc = lim["acceleration"].as<double>(300.0);
            l.max_jerk = lim["jerk"].as<double>(1000.0);
            limits.push_back(l);
        }
        return !limits.empty();
    } catch (const std::exception& e) {
        std::cerr << "[agent] profile load failed: " << e.what()
                  << std::endl;
        return false;
    }
}

}  // namespace

int main(int argc, char** argv) {
    std::string profile_path;
    int port = 50051;
    for (int i = 1; i < argc; ++i) {
        std::string a = argv[i];
        if (a == "--profile" && i + 1 < argc) profile_path = argv[++i];
        else if (a == "--port" && i + 1 < argc) port = std::stoi(argv[++i]);
    }
    if (profile_path.empty()) {
        std::cerr << "usage: controller_agent --profile <profile.yaml> [--port N]"
                  << std::endl;
        return 2;
    }

    std::vector<robottest::JointLimits> limits;
    int cycle_hz = 1000;
    std::string name;
    if (!load_profile(profile_path, limits, cycle_hz, name)) return 1;

    robottest::ControllerAgent agent(limits, cycle_hz, name);
    agent.start_control_loop();

    const std::string addr = "0.0.0.0:" + std::to_string(port);
    grpc::ServerBuilder builder;
    int actual = port;
    // gRPC fluent API（≥1.51）的 selected_port 输出参数在各版本语义不一致
    // （部分版本不返回实际端口），因此不做绑定结果检查——真实监听由客户端
    // wait_ready 验证；绑定失败表现为客户端连不上，由 conftest 候选端口重试吸收。
    builder.AddListeningPort(addr, grpc::InsecureServerCredentials(), &actual);
    // 机器可读行：客户端从 stdout 解析真实监听端口
    std::cout << "[agent] PORT=" << actual << std::endl;
    ControllerServiceImpl service(agent);
    builder.RegisterService(&service);
    // 同步服务模型：gRPC 为每个 RPC 分配独立线程池线程，
    // 长耗时 MoveAbsolute 不阻塞并发 Stop/GetState，无需显式 completion queue。
    // （此前 AddCompletionQueue 在本机 clang+mingw64 工具链上触发崩溃，已移除）

    std::unique_ptr<grpc::Server> server = builder.BuildAndStart();
    std::cout << "================ Robot Controller Agent ================"
              << std::endl;
    std::cout << "Profile       : " << name << std::endl;
    std::cout << "Controller    : Simulation" << std::endl;
    std::cout << "Cycle         : " << cycle_hz << " Hz" << std::endl;
    std::cout << "Dof           : " << limits.size() << std::endl;
    std::cout << "Listening     : " << addr << std::endl;
    std::cout << "========================================================="
              << std::endl;
    std::cout.flush();

    server->Wait();
    agent.stop_control_loop();
    return 0;
}
