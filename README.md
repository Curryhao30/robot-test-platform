# robot-test-platform

**机器人控制器与示教系统自动化验证平台（Robot Controller & Teach Pendant Validation Platform）**

面向多轴协作机器人控制器软件测试开发岗位的项目：以「控制器指令 → 仿真执行 → 状态采样 →
轨迹判定 → 协议校验 → 自动报告 → 缺陷回归」为闭环的软硬件解耦自动化验证平台。
当前实现覆盖 **P0 运动控制核心链路 + P1 现场总线/实时性/示教器/异常注入 +
P2 抽象/安全联锁/示教器协议模拟器**，共 **74 项自动化用例**，
本地与 GitHub Actions 双端全绿。

```
pytest (Test Case)
   │ gRPC（一次命令）
   ▼
C++ Controller Agent（控制环 1000Hz + PLCopen 状态机 + CiA402 + 虚拟 EtherCAT 主站）
   │
   ▼
Robot Simulator / Virtual EtherCAT Bus / Virtual Drive（被控对象）
   │ 批量轨迹 + 逐周期波形返回
   ▼
Test Oracle（位置/速度/轨迹/时序/状态机/总线故障判定）
   ▼
报告（result.json / trajectory.csv / report.html / log.txt / waveforms/*.svg）
```

## 设计原则

1. **Python 只编排、不跑控制循环**：Python 下发一次 `MoveAbsolute(target)`；
   控制周期内的执行与采样全部在 C++ Agent 内部控制环完成，完成后批量回传轨迹与
   逐周期数据。避免解释器与 RPC 调度影响实时执行。
2. **C++ 承担实时与确定性**：C++ Agent 负责控制环、运动执行、高频采样、
   CiA402 状态机、EtherCAT 周期交换与故障注入；Python 只做用例组织、调度与判定。
3. **Simulator 产生行为，Oracle 判定对错**：被控对象（机器人运动模型 / 虚拟从站 /
   虚拟总线）负责生成行为，Test Oracle 独立判定行为是否正确。
4. **Robot Profile 参数化**：机型（轴数/限位/限速/控制周期/精度阈值/接口）全部由
   `robot_profiles/*.yaml` 配置，测试引擎与 C++ Agent 读取同一份配置。
   P0 使用 `maira_sim`（MAiRA-like 仿真 Profile，**非华沿官方内部参数**）。
5. **软硬件解耦，逐层可替换**：P0 用运动学仿真、P1 用虚拟 EtherCAT 总线/虚拟从站，
   上层接口不变；P2 可替换为真实 EtherCAT 伺服 / 真实示教器 / 真实机器人（HIL）。

## 目录结构

```
robot-test-platform/
├── proto/controller.proto        # gRPC 协议：PLCopen 指令 + CiA402 + EtherCAT + 故障注入
├── simulator/                    # C++：RobotModel / TrajectoryGenerator / MotionExecutor
│   │                             #      Cia402StateMachine / ObjectDictionary / VirtualDrive
│   │                             #      VirtualEthercatBus（周期交换 + 实时统计 + 故障注入）
├── controller-agent/             # C++：控制环 + 状态机 + gRPC 服务（3 个 service）
├── orchestrator/                 # Python：Profile / gRPC 客户端 / Oracle / 报告 / 波形 / pytest
│   ├── app/                      #   profile.py client.py report.py waveform.py
│   ├── oracle/                   #   position.py velocity.py trajectory.py timing.py state_machine.py
│   ├── teach_pendant/            #   FastAPI 示教器后端 + static/index.html（五块 UI）
│   └── tests/                    #   74 项用例（P0 11 + CiA402 12 + EtherCAT 7 + 示教器 8 + 总线故障 6 + 波形 2 + Adapter 6 + SOEM 3 + 安全 7 + TP 9）
├── robot_profiles/maira_sim.yaml # 机型配置（7 轴仿真 Profile，cycle=1000Hz）
├── scripts/                      # generate_stubs.py / run_tests.ps1 / run_tests.sh
├── docs/design.md                # 软件设计文档（P0+P1）
└── reports/                      # 测试报告 + waveforms/
```

## 技术基线

| 层 | 选型 | 说明 |
|----|------|------|
| 执行层 | C++17 + CMake + gRPC/Protobuf | 控制环、CiA402、EtherCAT 主站、故障注入 |
| 编排层 | Python 3.11 + pytest | 用例组织、Oracle 判定、报告 |
| 示教器 | FastAPI + 原生 HTML/JS + Playwright | 五块 UI + 端到端自动化 |
| 报告 | 纯 Python（无 matplotlib） | result.json / CSV / HTML / SVG 波形 |
| 本机工具链 | MSYS2 clang64 + mingw64 包 | 详见 docs/design.md §7 |
| CI | GitHub Actions (ubuntu-latest) | push/PR 自动构建 + 全量 pytest |

## 快速开始（Windows 本机）

```powershell
# 0) 一次性环境
py -3.11 -m venv .venv
.venv\Scripts\python -m pip install -r orchestrator\requirements.txt
#    MSYS2 包: mingw-w64-x86_64-{grpc,protobuf,yaml-cpp,cmake}（clang64 提供 clang++）
#    Playwright: .venv\Scripts\python -m playwright install chromium

# 1) 一键：subst R: -> 构建 C++ Agent -> 生成桩 -> 全量 pytest（自动生成报告）
.\scripts\run_tests.ps1
```

手动分步（等价命令，便于调试）：

```powershell
subst R: "C:\Users\jh\测试开发\robot-test-platform"   # 规避中文路径乱码
cmake -S R:/ -B R:/build -G "Unix Makefiles" -DCMAKE_BUILD_TYPE=Release `
      -DCMAKE_MAKE_PROGRAM='C:/Users/jh/msys64/usr/bin/make.exe' `
      -DCMAKE_TOOLCHAIN_FILE='C:/Users/jh/测试开发/robot-test-platform/scripts/toolchain-clang-mingw64.cmake'
cmake --build R:/build --target controller_agent -j 8
cd orchestrator; ..\.venv\Scripts\python -m pytest -v
```

运行前 PATH 需包含 `msys64\clang64\bin`、`msys64\mingw64\bin`、`msys64\usr\bin`。
agent 进程由 conftest 自动拉起（候选端口 50051/50551/51051/51551/52051/52551 自动重试，
`RTP_AGENT_BIN` 可覆盖路径）。

## 测试矩阵（48 项）

### P0 运动控制核心（10 项）—— `tests/test_p0_core.py`

| # | 用例 | 覆盖点 |
|---|------|--------|
| 1 | test_servo_enable | Servo 使能 |
| 2 | test_axis_home | MC_Home 回零 |
| 3 | test_move_absolute_basic | MC_MoveAbsolute 基本执行 |
| 4 | test_position_accuracy | Position Oracle：终位误差 ≤ Profile 阈值 |
| 5 | test_velocity_limit | Velocity Oracle：请求超速 → 控制器钳制 |
| 6 | test_joint_limit_protection | 限位越界 → Error(ErrorID)，不得运动 |
| 7 | test_stop_during_motion | 运动中 Stop → CommandAborted |
| 8 | test_move_without_enable | 未使能运动 → NOT_ENABLED |
| 9 | test_emergency_stop_and_reset | 急停 FAULT → 拒绝运动 → Reset 恢复 |
| 10 | test_new_command_aborts_previous | 运动中新命令 → 旧命令 CommandAborted |

### P1-1 CiA402 驱动状态机（12 项）—— `tests/test_cia402.py`

覆盖 CiA402 全状态迁移表：`NotReadyToSwitchOn → SwitchOnDisabled → ReadyToSwitchOn →
SwitchedOn → OperationEnabled`，含 `Fault → FaultReset` 恢复、控制字/状态字位语义
（bit0 ready / bit1 switched on / bit2 operation enabled / bit3 fault / bit6 switch on disabled）、
对象字典（0x6040/0x6041/0x6060/0x6064/0x607A）、FaultReset 上升沿、非法迁移拒绝、
7 个独立虚拟从站一致性。

### P1-2 虚拟 EtherCAT 总线（7 项）—— `tests/test_ethercat.py`

- 总线信息（7 从站 / 名义周期）；
- PDO 周期交换：controlword → statusword / 状态名透传；目标位置 0x607A → 0x6064 直通；
- `RunCycles` 真实计时 + 自旋等待：实际频率、jitter min/max/mean/std、latency、overrun；
- 高频率多从站稳定性（500/1000Hz，7 从站全交换）。

### P1-3 实时性量化（并入 test_ethercat.py）

`RunCycles` 返回逐周期 `interval_us`（周期间隔）与 `processing_us`（单周期交换处理耗时）。
断言：500Hz 周期 2000us 下 overrun==0 且 latency_max < 2000us；1000Hz 同理。
本机实测（2026-09）：**500Hz jitter max≈87us、latency max≈3us、overrun=0；
1000Hz 7 从站 latency max≈6us、overrun=0**。

### P1-4 虚拟示教器 + Playwright（8 项）—— `tests/test_teach_pendant.py`

FastAPI 后端 + 原生 HTML/JS 五块 UI（Jog / Program / I/O / Robot / Alarm），
Playwright 端到端 8 用例：五块布局、tab 切换、**Servo ON 真实使能控制器**、
**点 Jog+ → 控制器关节 +5°（UI 与 gRPC 双重断言）**、运动中 STOP 实际停止、
未使能 Jog → Alarm 联动报警、Program 运行到位、Robot 信息展示。
核心语义：**UI 测试验证 UI 操作真实改变 Controller State**，而非只验证按钮点击。

### P1-5 总线异常注入（6 项）—— `tests/test_bus_fault.py`

| 故障 | 语义 | 用例 |
|------|------|------|
| SLAVE_LOSS | 指定从站无响应（LOST），其余从站照常交换 | 丢失隔离 / 恢复 |
| LINK_LOSS | 整条总线不可用，所有交换失败 | 阻断交换 / 阻断 RunCycles |
| BUS_ERROR | 帧级错误，交换失败 | 失败 / 清除恢复 |

含注入-清除循环、非法参数拒绝、用例间从站状态隔离（autouse teardown 复位）。

### P2.0 Robot Adapter 抽象（6 项）—— `tests/test_adapter.py`

`app/adapters/`（base.py / grpc_adapter.py / `__init__.py`）：用例层唯一依赖
`RobotAdapter`，`ControllerClient` 只存在于 conftest 与 grpc_adapter 内部。
- `adapter_config(profile)`：解析 `profile.adapter.type`（simulation | hil），
  非法类型 / HIL 缺 endpoint 直接报错；
- `connect_hil(profile)`：HIL 端点不可达时抛清晰错误（不挂死、不静默回退）；
- conftest 按 adapter 类型选择：simulation 拉起 C++ Agent 并包装为
  GrpcAdapter；hil 直连真机盒子——**48 项 P0/P1 用例零改动切换**。

### P2.1a SOEM EtherCAT 主站骨架（3 项）—— `tests/test_p2_soem.py`

`EthercatMaster` 抽象（simulator/include/robottest/ethercat_master.hpp）：
`exchange` / `run_cycles` / `inject_fault` / `mode` 虚接口，
`VirtualEthercatBus`（仿真）与 `SoemEthercatBus`（真机骨架）各自实现，
`main.cpp` 以 `--bus virtual|soem` 切换（`--iface`/`--slaves` 参数）。
SOEM 骨架：参数校验 + Linux raw socket 网卡探测 + Windows 明确不支持；
未连接时 exchange 返回清晰错误，不挂死、不冒充真机已连接。
用例：网卡缺失 FATAL 退出含原因 / 失败日志含可操作提示 / 非法 --bus 拒绝。

### P2.3 示教器协议模拟器（9 项）—— `tests/test_tp_protocol.py`

TP/1.0 行分隔 JSON 帧协议（自研仿真协议，非华沿私有协议）：
`teach_pendant/protocol.py` 编解码（坏帧拒绝、粘包/半包重组）→
`tp_bridge.py` TCP 协议桥（TP 帧 ↔ RobotAdapter）→
`tp_simulator.py` 协议客户端模拟器（CLI 可独立跑 jog_demo 脚本）。
协议线真实改变控制器状态（SERVO/JOG/STOP 同 UI 用例语义），双客户端
（浏览器 UI / 协议模拟器）共享控制器契约；未知动词拒绝、控制器错误透传、
STATE 推送帧。真示教器接入 = 厂商报文 → 本契约（RealTPBridge，待厂商资料）。

### P2.2a 安全联锁仿真（7 项）—— `tests/test_safety.py`

`SafetyStateMachine`（simulator/）：安全输入（急停/安全门/抱闸/驱动器故障）→
联锁输出（允许使能/停机/抱闸请求），优先级 **ESTOP > 门 > 驱动器故障**；
`SafetyService` 三 RPC（GetSafetyState / SetSafetyInput 仿真注入）；
`stop_required` 上升沿联动控制器：ESTOP → 急停（FAULT+掉使能，复位后可恢复），
门开 → Guard Stop（正常停止语义）。
修复 P0 隐藏 bug：`reset()` 未清 `emergency_requested_`，复位后首次运动被急停打断
（安全联动暴露，新增回归用例 test_emergency_reset_allows_relaunch）。

### P1-6 波形可视化（2 项）—— `tests/test_waveform.py`

`RunCycles` 逐周期数据 → 零依赖 SVG 波形（`reports/waveforms/jitter_*.svg` /
`latency_*.svg`）：jitter 波形（vs 名义周期）、latency 波形（overrun 阈值红线 +
超标段红色高亮）。断言数组长度、统计自洽、SVG 生成。

## 报告

每次 pytest 会话结束自动生成 `reports/run_YYYYMMDD_NNN/`：

- `report.html` — 浏览器直接打开（用例明细 + 汇总）
- `result.json` — 结构化结果（含每用例 detail）
- `trajectory.csv` — MoveAbsolute 精度用例完整轨迹采样
- `log.txt` — 文本结果
- `waveforms/*.svg` — jitter / latency 逐周期波形（P1-6）

## 版本历史（tag）

| tag | 内容 | CI |
|-----|------|-----|
| v0.1.0-p0 | P0 全链路：pytest→gRPC→C++ Agent→Simulator→Oracle→报告 | ✅ |
| v0.2.0-p1 | CiA402 状态机 + 对象字典 + 7 虚拟从站 | ✅ |
| v0.3.0-p1 | 虚拟 EtherCAT 总线（PDO 周期交换 + RunCycles） | ✅ |
| v0.4.0-p1 | 实时性量化：jitter/latency/overrun | ✅ |
| v0.5.0-p1 | 虚拟示教器（五块 UI）+ Playwright 自动化 | ✅ |
| v0.6.0-p1 | 总线异常注入（LINK_LOSS/SLAVE_LOSS/BUS_ERROR + 恢复） | ✅ |
| v0.7.0-p1 | 逐周期波形可视化（SVG） | ✅ |
| v0.8.0-p2-plan | P2 规划基线（Adapter/HIL/FSoE/示教器） | ✅ |
| v0.9.0-p2.0 | **Robot Adapter 抽象**：simulation/hil 配置切换，用例零改动 | ✅ |
| v0.10.0-p2.1a | **EthercatMaster 抽象 + SOEM 骨架**：--bus 切换，无网卡/非 Linux 清晰 FATAL | ✅ |
| v0.11.0-p2.2a | **SafetyService 安全联锁仿真**：ESTOP>门>驱动器故障联锁 + 控制器联动 | ✅ |
| v0.12.0-p2.3 | **示教器协议模拟器**：TP/1.0 协议桥 + 协议客户端，UI/REST 之外的第二条示教器线 | ✅ |

## 面试材料

- `docs/interview-project-brief.md` — **面试版项目说明书**：一分钟讲法、架构详解、
  74 用例矩阵、12 tag 演进、高频追问应答（25 条）、简历压缩版、面试红线。
- `docs/design.md` — 软件设计文档；`docs/p2-design.md` — P2 规划与验收。

## 后续阶段（未实现）

- **P2 剩余 HIL**（详见 `docs/p2-design.md`）：P2.1b 真 EtherCAT HIL（SOEM 接入
  真实从站，jitter/latency 变真机验收指标，需硬件）；P2.2b 真实安全 I/O
  （需硬件）；RealTPBridge 真示教器（需华沿协议厂商资料）；
- 运动学：FK/IK、TCP 轨迹 Oracle、MoveLinear/MoveCircular 路径判定；
- 管理侧：Requirement→TestCase→TestRun→Defect→Build→Release 可追溯闭环。
