# 软件设计文档：机器人控制器与示教系统自动化验证平台（P0）

> 文档编号：RTP-DESIGN-P0-001 ｜ 版本：v0.1 ｜ 状态：设计基线（P0 已实现）
> 对应岗位职责：控制器软件测试开发 / 示教器软件测试开发 / BUG 与版本管理 / 软件设计文档

## 1. 背景与目标

### 1.1 背景
协作机器人控制器软件（运动控制、PLCopen 指令、状态机）与示教器软件迭代快、
回归量大，且存在"现场问题不可复现、精度/速度等指标无法量化验证"的痛点。
岗位要求覆盖：控制器开发测试、示教器开发测试、软件 BUG 与版本管理、
软件设计文档编写。

### 1.2 目标（P0）
实现一条可演示、可扩展的自动化验证链路：

```
测试用例 → Python Orchestrator → C++ Controller Agent → Robot Simulator
        → PLCopen 状态机 → Test Oracle → 测试结果 → 缺陷/版本（后续）
```

关键约束：**Python 不得运行控制周期**。控制周期内的执行与采样全部在
C++ Agent 内部完成，完成后批量回传轨迹（timestamp[]/joint[]/状态[]），
保证"解释器与 RPC 调度不影响实时执行"这一论断可被面试与评审验证。

## 2. 总体架构

```
┌─────────────────────────────────────────────┐
│ Web Console（Vue3）—— P1 引入，P0 为 pytest  │
├─────────────────────────────────────────────┤
│ Test Orchestrator（Python/Pytest）           │
│  Case 调度 · Profile 加载 · 结果分析         │
├─────────────── gRPC（一次命令）──────────────┤
│ C++ Controller Agent                        │
│  ControlLoop(1000Hz) · PLCopen 状态机       │
│  MotionExecutor · DataRecorder（高频采样）   │
├─────────────────────────────────────────────┤
│ Robot Simulator（被控对象 Plant）            │
│  RobotModel · TrajectoryGenerator(梯形剖面) │
├─────────────────────────────────────────────┤
│ Test Oracle（判定层）                        │
│  Position / Velocity / Trajectory / Timing  │
│  StateMachine                               │
└─────────────────────────────────────────────┘
```

### 2.1 分层职责

| 层 | 组件 | 职责 |
|----|------|------|
| 编排层 | Python Orchestrator | 加载 Robot Profile、下发命令、接收批量轨迹、调用 Oracle 判定、生成报告 |
| 执行层 | C++ Controller Agent | 控制环（cycle_hz 可配）、PLCopen 状态机、轨迹规划调度、高频采样 |
| 被控对象 | Robot Simulator | 多轴运动模型（P0：梯形速度剖面同步 PTP；无 DH/FK） |
| 判定层 | Test Oracle | 独立于仿真器，负责"行为是否正确"的判定 |

### 2.2 控制周期职责边界
- Python：仅一次 `MoveAbsolute(target, velocity, acc, dec, jerk)`；
- C++：`while(running){ update_motion(); update_robot_state(); sample(); }`
  以 cycle_hz（默认 1000Hz）运行；运动完成后批量返回轨迹；
- 判定：Python 基于批量轨迹做统计与阈值判定（如 max|dq/dt| ≤ Vmax）。

## 3. 协议设计（proto/controller.proto）

| RPC | 语义 | 返回 |
|-----|------|------|
| Enable/Disable | 伺服使能/去使能 | CommandResult |
| Home | MC_Home 回零运动 | CommandResult |
| MoveAbsolute | 绝对定位（PLCopen MC_MoveAbsolute） | CommandResult + 轨迹 |
| MoveRelative | 相对运动 | CommandResult + 轨迹 |
| Stop | normal=CommandAborted / emergency=FAULT | CommandResult |
| Reset | FAULT 复位 | CommandResult |
| GetState | 当前状态快照 | RobotState |

`CommandResult.samples`：控制环按 cycle_hz 批量采样的轨迹
（timestamp_ns / joints / motion_state），一次命令一次批量返回。

错误码：0=OK 1=NOT_ENABLED 2=JOINT_LIMIT 3=BUSY 4=ABORTED 5=EMERGENCY_STOP 6=FAULT 7=UNKNOWN

## 4. 核心模块设计

### 4.1 C++ Controller Agent（controller-agent/）
- `ControllerAgent`：命令入口 + 状态机 + 控制环线程；
- `DataRecorder`：按 motion_id 隔离的采样缓冲（保证被新命令中止的旧运动
  仍可取回自身轨迹）；
- 并发模型：`std::mutex + condition_variable`；控制环线程独占高频采样；
  阻塞的 MoveAbsolute 与并发 Stop/GetState 通过多 completion queue 支持。

### 4.2 Robot Simulator（simulator/）
- `RobotModel`：各轴状态 + 限位/限速配置；`within_position_limits`、
  `clamp_velocities`（请求超速 -> 钳制到轴限速）；
- `TrajectoryGenerator`：梯形速度剖面，**同步 PTP**（各轴按自身限制计算
  时长，按最慢轴统一时长反解缩放速度，保证所有轴同时起停）；
- `MotionExecutor`：按控制周期推进规划，输出各轴目标状态（P0 理想被控对象）。

### 4.3 Test Oracle（orchestrator/oracle/）
| Oracle | 判定项 | P0 用例 |
|--------|--------|---------|
| position | \|q_final - q_target\| ≤ ε_profile | test_position_accuracy |
| velocity | max\|dq/dt\| ≤ Vmax_profile × (1+tol) | test_velocity_limit |
| trajectory | 无位置突变、终点单调收敛 | （随精度用例运行） |
| timing | 采样周期均值 ≈ 1/cycle_hz，jitter ≤ 容差 | （预留，P0 宽松） |
| state_machine | BUSY→ACTIVE→DONE / ABORTED / ERROR 序列合法 | test_stop_during_motion 等 |

### 4.4 Robot Profile（robot_profiles/maira_sim.yaml）
配置中心：轴数、控制周期、各轴限位/限速/加减速、精度阈值、接口、驱动 Profile。
P0 使用 `maira_sim`（MAiRA-like 仿真配置，**非华沿官方内部参数**）；
后续机型（如 huayan_maira_public / huayan_elfin_pro_public）通过新增
YAML 扩展，测试引擎无需改动。

## 5. PLCopen 状态机（P0）

运动状态：IDLE / BUSY / ACTIVE / DONE / ABORTED / ERROR
控制器状态：DISABLED / ENABLED / FAULT

| 场景 | 期望 |
|------|------|
| 正常执行 | Execute -> Busy -> Active -> Done |
| 运动中 Stop | Busy/Active -> CommandAborted（ABORTED） |
| 运动中新命令(abort) | 旧命令 CommandAborted，新命令正常执行 |
| 目标越限位 | Error + ErrorID=JOINT_LIMIT，机器人不得运动 |
| 未使能运动 | Error + ErrorID=NOT_ENABLED |
| 急停 | FAULT；拒绝运动；Reset 后恢复 |

## 6. 测试设计

运行方式：`pytest`（orchestrator/），conftest 自动拉起 `controller_agent.exe
--profile ... --port 50051`，等待 gRPC 就绪后执行 10 项 P0 用例，会话结束
自动生成报告（reports/run_YYYYMMDD_NNN/）。

用例矩阵见 README.md「P0 用例清单」。

## 7. 部署与构建

- 构建：CMake ≥ 3.25，C++17；`controller_agent` 依赖
  gRPC / protobuf / yaml-cpp；
- **本机工具链（实际落地）**：MSYS2 **clang64(clang++) + mingw64 预编译
  gRPC/protobuf/yaml-cpp**。决策链：本机网络无法从 GitHub 获取 vcpkg/gRPC
  源码（vcpkg/MSVC 路线放弃）→ 改用 MSYS2 预编译包 → 但 MSYS2 的 GCC 16.2
  全家（gcc/g++/cc1/ninja）在本机无法启动（加载器异常 0xc0000135，已排查
  导入表/PE 头/WDAC 均正常，判定机器级怪癖）→ 最终采用 clang++（可正常
  运行）+ `-stdlib=libstdc++` 对齐 mingw64 的 GCC ABI；
- **关键兼容性修复（本机已验证）**：
  - 移除 gRPC `AddCompletionQueue`：本工具链下注册服务 + 显式 CQ 会在
    BuildAndStart 崩溃（0xC0000005）；同步服务模型每个 RPC 有独立线程池
    线程，阻塞 MoveAbsolute 不阻塞并发 Stop/GetState；
  - `--unwindlib=libgcc`：避免 clang64 的 libunwind.dll 与 mingw64 的
    libstdc++/libgcc_s_seh 双解卷器冲突；
  - **中文路径**：MSYS2 make 传参给 protoc 时中文路径乱码（测试开发→���），
    构建统一在 `subst R:`（ASCII）下进行；
- 代码与 CMake 完全可移植，网络恢复后可在 MSVC+vcpkg、WSL2 Ubuntu 下
  以同一 CMakeLists 构建（Linux 验证路径）；
- Python：3.11 venv，依赖见 orchestrator/requirements.txt。
- 一键运行：`scripts/run_tests.ps1`（subst R: → 构建 → 桩 → pytest）。

## 7.1 P0 验收结论（2026-09-15）

- `pytest`：**10 passed in 12.38s**，全绿；
- 报告：`reports/run_20260915_006/`（report.html / result.json /
  trajectory.csv / log.txt）已生成；
- 关键论断已验证：Python 只下发一次 MoveAbsolute；1000Hz 执行与采样全部
  在 C++ Agent 控制环线程内完成（3.2s 运动产生 3200 采样、wall 时长 3.18s）；
  停止/急停/新命令中止均返回 ABORTED/ERROR 并带终态采样；
- 状态机终态按 motion_id 隔离（`outcomes_` 表），并发新命令不会覆盖
  被中止旧命令的终态。

## 8. 验收标准（P0）

1. `pytest` 全绿：10 项用例通过；
2. 报告目录生成 result.json / trajectory.csv / report.html / log.txt；
3. 关键论断可验证：Python 侧无 1000Hz 循环；轨迹采样来自 C++ Agent；
4. 换一个 Profile（新 YAML）不改测试代码即可运行（Profile 参数化）。

## 9. 后续演进（P1/P2）

- P1：EtherCAT/CoE 虚拟从站 + CiA402 状态机对象字典测试；控制周期
  jitter/latency 测量；虚拟示教器 + Playwright UI 自动化（Jog/Program/
  I/O/Status/Alarm 五块）；异常注入；长稳测试；
- P2：真实 EtherCAT Servo HIL；真实示教器/机器人；FSoE/Safety I/O；
- 运动学：FK/IK、TCP 轨迹 Oracle、MoveLinear/MoveCircular；
- 管理侧：Requirement→TestCase→TestRun→Defect→Build→Release 可追溯闭环
  （Test Run 失败自动创建缺陷、修复版本关联、回归触发）。
