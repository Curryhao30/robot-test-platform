# robot-test-platform

**机器人控制器与示教系统自动化验证平台（P0）**

面向多轴机器人控制器软件测试开发。P0 先跑通一条完整链路：

```
pytest (Test Case)
   │ gRPC (一次命令)
   ▼
C++ Controller Agent（控制环 1000Hz + PLCopen 状态机 + 采样）
   │
   ▼
Robot Simulator（多轴梯形速度剖面运动模型）
   │ 批量轨迹返回（timestamp[]/joint[]/状态[]）
   ▼
Test Oracle（位置/速度/轨迹/时序/状态机判定）
   ▼
报告（result.json / trajectory.csv / report.html / log.txt）
```

## 设计原则

- **Python 只编排、不跑控制循环**：Python 下发一次 `MoveAbsolute(target)`；
  1000Hz 控制周期内的执行与采样全部在 C++ Agent 内部控制环完成，
  完成后批量回传轨迹。避免解释器与 RPC 调度影响实时执行。
- **Simulator 产生行为，Oracle 判定对错**：仿真器负责生成轨迹与状态，
  Test Oracle 独立判定位置误差、速度约束、状态序列合法性等。
- **Robot Profile 参数化**：机型（轴数/限位/限速/控制周期/精度阈值）全部
  由 `robot_profiles/*.yaml` 配置，测试引擎与 C++ Agent 读取同一份配置。
  P0 使用 `maira_sim`（MAiRA-like 仿真 Profile，**非华沿官方参数**）。

## 目录结构

```
robot-test-platform/
├── proto/controller.proto        # gRPC 协议（PLCopen 指令 + 轨迹批量回传）
├── simulator/                    # C++：RobotModel / TrajectoryGenerator / MotionExecutor
├── controller-agent/             # C++：控制环 + PLCopen 状态机 + gRPC 服务
├── orchestrator/                 # Python：Profile 加载 / gRPC 客户端 / Oracle / 报告 / pytest
│   ├── app/                      #   profile.py client.py report.py
│   ├── oracle/                   #   position.py velocity.py trajectory.py timing.py state_machine.py
│   └── tests/                    #   P0 核心用例（10 项）
├── robot_profiles/maira_sim.yaml # 机型配置（P0 仿真 Profile）
├── scripts/                      # generate_stubs.py / run_tests.ps1 / run_tests.sh
├── docs/design.md                # 软件设计文档
└── reports/run_YYYYMMDD_NNN/     # 测试报告输出
```

## 快速开始（Windows 本机）

工具链：**MSYS2 clang64(clang++) + mingw64(libstdc++/gRPC/protobuf/yaml-cpp)**。
原因：本机 MSYS2 GCC16.2 全家无法启动（加载器异常 0xc0000135），clang 正常；
且网络环境导致 vcpkg/MSVC 依赖拉取失败，故统一用 MSYS2 包（见 docs/design.md）。

```powershell
# 0) 一次性环境
#    - Python 3.11 venv:  py -3.11 -m venv .venv
#      .venv\Scripts\python -m pip install -r orchestrator\requirements.txt
#    - MSYS2 包: mingw-w64-x86_64-{grpc,protobuf,yaml-cpp,cmake}（clang64 提供 clang++）

# 1) 一键：subst R: -> 构建 C++ Agent -> 生成桩 -> 运行 10 项 pytest（自动生成报告）
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
agent 进程由 conftest 自动拉起（默认找 `build/controller-agent/controller_agent.exe`，
可用环境变量 `RTP_AGENT_BIN` 覆盖）。

## P0 用例清单（10 项）

| # | 用例 | 覆盖点 |
|---|------|--------|
| 1 | test_servo_enable | Servo 使能 |
| 2 | test_axis_home | MC_Home 回零 |
| 3 | test_move_absolute_basic | MC_MoveAbsolute 基本执行 |
| 4 | test_position_accuracy | Position Oracle：终位误差 ≤ Profile 阈值 |
| 5 | test_velocity_limit | Velocity Oracle：请求超速 -> 控制器钳制 |
| 6 | test_joint_limit_protection | 限位越界 -> Error(ErrorID)，不得运动 |
| 7 | test_stop_during_motion | 运动中 Stop -> CommandAborted |
| 8 | test_move_without_enable | 未使能运动 -> NOT_ENABLED |
| 9 | test_emergency_stop_and_reset | 急停 FAULT -> 拒绝运动 -> Reset 恢复 |
| 10 | test_new_command_aborts_previous | 运动中新命令 -> 旧命令 CommandAborted |

## P0 验证记录

```
10 passed in 12.38s
[PASS] test_servo_enable            [PASS] test_axis_home
[PASS] test_move_absolute_basic     [PASS] test_position_accuracy
[PASS] test_velocity_limit          [PASS] test_joint_limit_protection
[PASS] test_stop_during_motion      [PASS] test_move_without_enable
[PASS] test_emergency_stop_and_reset [PASS] test_new_command_aborts_previous
```

样例报告：`reports/run_20260915_006/report.html`（浏览器直接打开）。
真实数据示例：位置精度用例各轴终位误差 0.0000°（阈值 0.01°）；速度限速用例
轴 0 峰值 |v|=107.1°/s（限速 120，请求 400 被钳制）；限位保护用例越界目标
200° 报 Error(2) 且位置不变。

## 报告

每次 pytest 会话结束自动生成 `reports/run_YYYYMMDD_NNN/`：

- `report.html` — 浏览器直接打开
- `result.json` — 结构化结果
- `trajectory.csv` — MoveAbsolute 精度用例的完整轨迹采样
- `log.txt` — 文本结果

## 后续阶段（未实现）

- P1：EtherCAT/CoE 虚拟从站 + CiA402 状态机对象字典；jitter/latency 测量；
  虚拟示教器 + Playwright UI 自动化；异常注入；长稳测试
- P2：真实 EtherCAT Servo HIL；真实示教器/机器人；FSoE/Safety I/O
- 机器人运动学：FK/IK、TCP 轨迹、MoveLinear/MoveCircular 路径 Oracle
