# 软件设计文档：机器人控制器与示教系统自动化验证平台（P0+P1）

> 文档编号：RTP-DESIGN-001 ｜ 版本：v0.7 ｜ 状态：设计基线（P0、P1 已实现）
> 对应岗位职责：控制器软件测试开发 / 示教器软件测试开发 / BUG 与版本管理 / 软件设计文档

## 1. 背景与目标

### 1.1 背景
协作机器人控制器软件（运动控制、PLCopen 指令、状态机）与示教器软件迭代快、
回归量大，且存在"现场问题不可复现、精度/速度/实时性等指标无法量化验证"的痛点。
岗位要求覆盖：控制器开发测试、示教器开发测试、软件 BUG 与版本管理、软件设计文档编写。

### 1.2 目标
实现一条可演示、可扩展、可逐层替换真机的自动化验证链路：

```
测试用例 → Python Orchestrator → C++ Controller Agent
        → Robot Simulator / Virtual EtherCAT Bus → 状态机 → Test Oracle
        → 测试结果 → 报告 / 波形 → 缺陷与版本（P2 管理侧）
```

关键约束：**Python 不得运行控制周期**。控制周期内的执行、采样、总线交换与
故障注入全部在 C++ Agent 内部完成，完成后批量回传轨迹与逐周期数据，
保证"解释器与 RPC 调度不影响实时执行"这一论断可被面试与评审验证。

## 2. 总体架构

```
┌──────────────────────────────────────────────────────────┐
│ Virtual Teach Pendant（FastAPI + HTML/JS + Playwright）    │  P1
│  Jog / Program / I/O / Robot / Alarm                       │
├──────────────────────────────────────────────────────────┤
│ Test Orchestrator（Python/Pytest）                         │
│  Case 调度 · Profile 加载 · Oracle 判定 · 报告 / 波形      │
├─────────────────── gRPC（一次命令）────────────────────────┤
│ C++ Controller Agent                                      │
│  ControlLoop(cycle_hz) · PLCopen 状态机                   │
│  Cia402StateMachine ×7 · ObjectDictionary                 │
│  VirtualEthercatBus（PDO 交换 + 实时统计 + 故障注入）       │
├──────────────────────────────────────────────────────────┤
│ 被控对象（Plant）                                          │
│  Robot Simulator（梯形速度剖面）                           │
│  VirtualDrive ×7（CiA402 从站仿真，0x607A→0x6064 直通）     │
├──────────────────────────────────────────────────────────┤
│ Test Oracle（判定层）                                      │
│  Position / Velocity / Trajectory / Timing / StateMachine │
│  CiA402 迁移合法性 · 总线故障语义                          │
└──────────────────────────────────────────────────────────┘
```

### 2.1 分层职责

| 层 | 组件 | 职责 |
|----|------|------|
| 人机交互 | Virtual Teach Pendant | 五块 UI，操作经 REST 桥接真实控制器状态 |
| 编排层 | Python Orchestrator | 加载 Profile、下发命令、接收批量数据、Oracle 判定、报告/波形 |
| 接入层 | Robot Adapter（P2.0） | simulation（本地 Agent）/ hil（真机盒子），用例层唯一依赖 |
| 执行层 | C++ Controller Agent | 控制环、PLCopen 状态机、CiA402 状态机、EtherCAT 周期交换、高频采样 |
| 被控对象 | Robot Simulator / VirtualDrive / VirtualEthercatBus | 运动模型与总线行为 |
| 判定层 | Test Oracle | 独立判定"行为是否正确" |

### 2.2 实时性边界（核心论断）
- Python：仅一次命令（MoveAbsolute / CycleExchange / RunCycles）；
- C++：`while(running){ update_motion(); sample(); }` 或
  `while(cycle){ exchange(); stats(); spin_until(deadline); }`；
- 判定：Python 基于批量回传数据做统计与阈值判定；
- 逐周期明细（interval_us / processing_us）由 C++ 侧记录，Python 侧绘制波形。

## 3. 协议设计（proto/controller.proto）

### 3.1 ControllerService（PLCopen 运动指令）
| RPC | 语义 | 返回 |
|-----|------|------|
| Enable/Disable | 伺服使能/去使能 | CommandResult |
| Home | MC_Home 回零运动 | CommandResult |
| MoveAbsolute | 绝对定位（MC_MoveAbsolute） | CommandResult + 轨迹 |
| MoveRelative | 相对运动 | CommandResult + 轨迹 |
| Stop | normal=CommandAborted / emergency=FAULT | CommandResult |
| Reset | FAULT 复位 | CommandResult |
| GetState | 当前状态快照 | RobotState |

`CommandResult.samples`：控制环按 cycle_hz 批量采样的轨迹（timestamp_ns / joints / motion_state）。
错误码：0=OK 1=NOT_ENABLED 2=JOINT_LIMIT 3=BUSY 4=ABORTED 5=EMERGENCY_STOP 6=FAULT 7=UNKNOWN

### 3.2 Cia402Service（P1-1）
| RPC | 语义 |
|-----|------|
| GetState / SetState | 读取/驱动单从站状态机 |
| WriteObject / ReadObject | 对象字典读写（0x6040/0x6041/0x6060/0x6064/0x607A） |
| InjectFault | 注入 Fault（测试 FaultReset 恢复路径） |

### 3.3 EthercatService（P1-2/3/5/6）
| RPC | 语义 |
|-----|------|
| BusInfo | 从站数 / 名义控制周期 |
| CycleExchange | 单周期 PDO 交换 |
| RunCycles | 连续 N 周期：实际频率 / jitter / latency / overrun / 逐周期数组 |
| InjectBusFault / ClearBusFault | 故障注入：LINK_LOSS / SLAVE_LOSS / BUS_ERROR |

`RunCyclesResponse` 关键字段：`actual_hz, jitter_min/max/mean/std_us, latency_min/max/mean/std_us,
overrun_cycles, interval_us[], processing_us[]`。

## 4. 核心模块设计

### 4.1 C++ Controller Agent（controller-agent/）
- `ControllerAgent`：命令入口 + PLCopen 状态机 + 控制环线程；
- `DataRecorder`：按 motion_id 隔离的采样缓冲（被新命令中止的旧运动仍可取回自身轨迹）；
- 并发模型：`std::mutex + condition_variable`；控制环线程独占高频采样；
- 挂载：1 ControllerService + 7 VirtualDrive（CiA402）+ 1 VirtualEthercatBus + 3 service。

### 4.2 CiA402 状态机（simulator/，P1-1）
9 状态（NotReadyToSwitchOn → SwitchOnDisabled → ReadyToSwitchOn → SwitchedOn →
OperationEnabled，含 QuickStop/Fault 分支）+ 显式迁移表：
| 当前 | 事件（控制字） | 目标 |
|------|----------------|------|
| SwitchOnDisabled | Shutdown (0x06) | ReadyToSwitchOn |
| ReadyToSwitchOn | SwitchOn (0x07) | SwitchedOn |
| SwitchedOn | Enable (0x0F) | OperationEnabled |
| 任意使能态 | Disable (0x00) | SwitchOnDisabled |
| 任意 | Fault (bit3) | Fault（FaultReset 上升沿 0x80 恢复） |

位语义：statusword bit0=ready / bit1=switched on / bit2=operation enabled /
bit3=fault / bit6=switch on disabled。非法迁移拒绝并保留原状态。

### 4.3 示教器协议模拟器（teach_pendant/，P2.3）
- TP/1.0 行分隔 JSON 帧协议（自研仿真协议，非华沿私有）：protocol.py 编解码
  （坏帧拒绝 + FrameStream 粘包/半包重组）；
- tp_bridge.py：TCP 协议桥（TP 帧 ↔ RobotAdapter），SERVO/JOG/STOP/STATE/
  RESET/HOME/QUIT；错误透传、未知动词拒绝、STATE 周期推送；
- tp_simulator.py：协议客户端模拟器（CLI `--script jog_demo`）；
- 双客户端（浏览器 UI / 协议模拟器）共享控制器契约；真示教器接入 =
  厂商报文 → 本契约（RealTPBridge，待厂商资料）。

### 4.3 安全联锁仿真（simulator/，P2.2a）
- `SafetyStateMachine`：输入 ESTOP/门/抱闸/驱动器故障 → 输出 允许使能/停机/抱闸；
  优先级 ESTOP > 门 > 驱动器故障；抱闸未释放 → 禁止使能（防坠落）；
- `SafetyService`：GetSafetyState / SetSafetyInput（仿真注入）；`stop_required`
  上升沿联动控制器（ESTOP → 急停 FAULT，门开 → Guard Stop）；复位可恢复；
- 边界：只验证安全逻辑与联锁语义，安全等级与真实 I/O 由 P2.2b HIL 验证。
- 修复 P0 bug：reset 清 emergency_requested_/stop_requested_/pending_abort_。

### 4.3 EtherCAT 主站抽象（simulator/，P1-2/3/5 + P2.1a）
- `EthercatMaster`（ethercat_master.hpp）：exchange / run_cycles / 故障注入 /
  mode 虚接口——gRPC EthercatService 与用例只依赖本抽象；
- `VirtualEthercatBus`（仿真实现，P1-2/3/5）与 `SoemEthercatBus`（真机骨架，
  P2.1a：参数校验 + Linux 网卡探测 + Windows 明确不支持）各自实现，
  `main.cpp --bus virtual|soem` 一行切换；SOEM 从站扫描留待 P2.1b；
- 未连接时 exchange 返回清晰错误，不挂死、不冒充真机已连接。

- `VirtualEthercatBus`：主站侧周期 PDO 交换（写 controlword/0x607A → 读 statusword/
  0x6064/状态名）；`run_cycles` 真实计时 + 自旋等待（避开 sleep 粒度污染 jitter）；
- 故障注入语义（对齐真实 EtherCAT）：
  - `SLAVE_LOSS`：该从站输入标记 LOST（无响应），其余从站照常交换（watchdog 隔离）；
  - `LINK_LOSS`：整条总线不可用，任何交换失败；
  - `BUS_ERROR`：帧级错误，交换失败；
  - 非法从站号 / 未知类型拒绝注入；清除后总线恢复正常。

### 4.4 Robot Simulator（simulator/）
- `RobotModel`：各轴状态 + 限位/限速配置；`within_position_limits`、
  `clamp_velocities`（请求超速 → 钳制到轴限速）；
- `TrajectoryGenerator`：梯形速度剖面，同步 PTP（按最慢轴统一时长反解缩放速度）；
- `MotionExecutor`：按控制周期推进规划（P0 理想被控对象）。

### 4.5 Test Oracle（orchestrator/oracle/）
| Oracle | 判定项 | 用例 |
|--------|--------|------|
| position | \|q_final - q_target\| ≤ ε_profile | test_position_accuracy |
| velocity | max\|dq/dt\| ≤ Vmax_profile × (1+tol) | test_velocity_limit |
| trajectory | 无位置突变、终点单调收敛 | （随精度用例运行） |
| timing | 采样周期均值 ≈ 1/cycle_hz | （P0 宽松） |
| state_machine | BUSY→ACTIVE→DONE / ABORTED / ERROR 序列合法 | test_stop_during_motion 等 |
| cia402 | 迁移合法性 / 状态字位语义 / FaultReset 上升沿 | test_cia402.py（12） |
| bus_fault | 丢失隔离 / 断链阻断 / 错误恢复 | test_bus_fault.py（6） |

### 4.6 Robot Profile（robot_profiles/maira_sim.yaml）
配置中心：轴数、控制周期、各轴限位/限速/加减速、精度阈值、接口、驱动 Profile。
P0 使用 `maira_sim`（MAiRA-like 仿真配置，**非华沿官方内部参数**）；
后续机型通过新增 YAML 扩展，测试引擎无需改动（如 huayan_maira_public /
huayan_elfin_pro_public 可基于公开规格建立）。

### 4.7 虚拟示教器（orchestrator/teach_pendant/，P1-4）
- 后端：FastAPI，lifespan 启动时经环境变量注入 Controller Agent gRPC 通道与 Profile；
  REST：`/api/status`（控制器状态）、`/api/jog`（增量步进，先 stop 再 move_relative）、
  `/api/servo`、`/api/program`（预置程序）、`/api/io`、`/api/alarm`；
- 前端：单页五块 tab UI（Jog / Program / I/O / Robot / Alarm），轮询状态，
  未使能操作触发 Alarm tab 联动；
- 测试：Playwright chromium（无头），8 项端到端用例；
  核心语义：**UI 操作必须真实改变 Controller State**（Jog 步进后读 gRPC 关节位置断言）。

### 4.8 波形可视化（orchestrator/app/waveform.py，P1-6）
- 输入：RunCycles 逐周期 `interval_us[]` / `processing_us[]`；
- 输出：零依赖 SVG（`reports/waveforms/jitter_*.svg` / `latency_*.svg`）；
  jitter 波形（vs 名义周期参考线）、latency 波形（overrun 阈值红线 + 超标段红色高亮）；
- 不依赖 matplotlib：CI / 面试机零额外安装。

## 5. PLCopen 状态机

运动状态：IDLE / BUSY / ACTIVE / DONE / ABORTED / ERROR
控制器状态：DISABLED / ENABLED / FAULT

| 场景 | 期望 |
|------|------|
| 正常执行 | Execute → Busy → Active → Done |
| 运动中 Stop | Busy/Active → CommandAborted（ABORTED） |
| 运动中新命令(abort) | 旧命令 CommandAborted，新命令正常执行 |
| 目标越限位 | Error + ErrorID=JOINT_LIMIT，机器人不得运动 |
| 未使能运动 | Error + ErrorID=NOT_ENABLED |
| 急停 | FAULT；拒绝运动；Reset 后恢复 |

## 6. 测试设计

运行方式：`pytest`（orchestrator/）。conftest 自动拉起 `controller_agent.exe
--profile ... --port <候选端口>`，等待 gRPC 就绪；示教器测试额外拉起 uvicorn 子进程
（端口 58081-58083）+ Playwright；会话结束自动生成报告。

用例矩阵见 README.md「测试矩阵」。当前共 **83 项**：
P0 11 + CiA402 12 + EtherCAT 7 + 示教器 8 + 总线故障 6 + 波形 2 + Adapter 6 + SOEM 骨架 3 + 安全联锁 7 + TP 协议 9 + Console 9。

关键测试设计点：
- **实时性断言**（P1-3）：500Hz（周期 2000us）与 1000Hz 下 overrun==0 且
  latency_max < 周期，jitter 统计由逐周期明细重算校验；
- **异常注入隔离**（P1-5）：bus_fault 用例 autouse teardown 复位全部从站，
  避免污染 CiA402 初始态断言（文件收集顺序：bus_fault < cia402 < ...）；
- **波形自洽**（P1-6）：max(interval_us) - jitter_max ≈ 名义周期。

## 7. 部署与构建

- 构建：CMake ≥ 3.25，C++17；`controller_agent` 依赖 gRPC / protobuf / yaml-cpp；
- **本机工具链（实际落地）**：MSYS2 clang64(clang++) + mingw64 预编译
  gRPC/protobuf/yaml-cpp。决策链：本机网络无法从 GitHub 获取 vcpkg/gRPC 源码
  （vcpkg/MSVC 路线放弃）→ 改用 MSYS2 预编译包 → MSYS2 的 GCC 16.2 全家本机
  无法启动（0xc0000135）→ 采用 clang++ + `-stdlib=libstdc++` 对齐 mingw64 ABI；
- 关键兼容性修复（本机已验证）：
  - 移除 gRPC `AddCompletionQueue`（本工具链注册服务 + 显式 CQ 会在 BuildAndStart
    崩溃）；同步服务模型每个 RPC 独立线程池线程；
  - `--unwindlib=libgcc`：避免 clang64 libunwind 与 mingw64 libstdc++/libgcc_s_seh
    双解卷器冲突；
  - 中文路径：构建统一在 `subst R:`（ASCII）下进行（MSYS2 make/protoc 中文乱码）；
  - **gRPC fluent API**：AddListeningPort 返回 ServerBuilder& + 第 3 参 selected_port，
    不得用 selected==0 判绑定失败（mingw64 1.82 恒 0）；
- 代码与 CMake 完全可移植，同一 CMakeLists 可在 MSVC+vcpkg、WSL2 Ubuntu 构建
  （Linux 验证路径）；
- Python：3.11 venv，依赖见 orchestrator/requirements.txt；
- CI：GitHub Actions（ubuntu-latest）——装依赖 → CMake configure/build →
  Playwright chromium 安装 → 全量 pytest → 上传 reports/ 产物；
- 一键运行：`scripts/run_tests.ps1`（subst R: → 构建 → 桩 → pytest）。

## 8. 验收标准

1. `pytest` 全绿：83 项用例通过（本机 + CI 双端）；
2. 报告目录生成 result.json / trajectory.csv / report.html / log.txt /
   waveforms/*.svg；
3. 关键论断可验证：Python 侧无控制周期循环；轨迹与逐周期数据来自 C++ Agent；
4. 换一个 Profile（新 YAML）不改测试代码即可运行；
5. 总线故障注入语义可验证（丢失隔离 / 断链阻断 / 恢复）。
6. Robot Adapter：用例层不出现 ControllerClient 直连；adapter.type 一行切换（P2.0）。

## 9. 版本记录

| tag | 内容 | 日期 |
|-----|------|------|
| v0.1.0-p0 | P0 全链路（gRPC fluent API 修复后基线） | 2026-09-15 |
| v0.2.0-p1 | CiA402 状态机 + 对象字典 + 7 虚拟从站 | 2026-09-22 |
| v0.3.0-p1 | 虚拟 EtherCAT 总线 | 2026-09-22 |
| v0.4.0-p1 | 实时性量化（jitter/latency/overrun） | 2026-09-22 |
| v0.5.0-p1 | 虚拟示教器 + Playwright | 2026-09-22 |
| v0.6.0-p1 | 总线异常注入 | 2026-09-22 |
| v0.7.0-p1 | 逐周期波形可视化 | 2026-09-22 |
| v0.8.0-p2-plan | P2 规划基线 | 2026-09-22 |
| v0.9.0-p2.0 | Robot Adapter 抽象（simulation/hil 切换） | 2026-09-23 |
| v0.10.0-p2.1a | EthercatMaster 抽象 + SOEM 骨架（--bus 切换） | 2026-09-23 |
| v0.11.0-p2.2a | SafetyService 安全联锁仿真（ESTOP>门>驱动器故障 + 控制器联动） | 2026-09-23 |
| v0.12.0-p2.3 | 示教器协议模拟器（TP/1.0 协议桥 + 协议客户端，9 用例） | 2026-09-23 |
| v0.13.0-p3 | 管理控制台（console 只读 API + 零依赖单页前端；报告写盘 RTP_WRITE_REPORT 门控） | 2026-09-23 |
| v0.14.0-p3-ops | 可操作控制台（触发运行/分组、进度轮询、运行明细、缺陷人工关闭·重开） | 2026-09-23 |

## 10. 后续演进（P2 / 管理侧）

- P2：真实 EtherCAT Servo HIL（上层接口已解耦，替换 Robot Adapter 即可）；
  真实示教器/机器人；FSoE/Safety I/O 联锁（安全逻辑与状态联锁仿真 → HIL 阶段
  验证真实 I/O）；
- 运动学：FK/IK、TCP 轨迹 Oracle、MoveLinear/MoveCircular 路径判定；
- 管理侧：v0.14.0-p3-ops 落地可操作闭环（控制台触发后台 pytest 运行、
  进度轮询、运行明细、缺陷人工关闭/重开——人工状态以 defect_state.json 覆盖
  自动派生，数据源全部为 reports/run_*/result.json，不引入数据库）；
  Requirement→Build→Release 写操作（版本发布/分配）为后续演进；
- AI 缺陷分诊：明确不做（对控制器测试岗位，"CiA402 FaultReset 异常状态覆盖"
  比"用大模型分析 BUG"更有价值）。
