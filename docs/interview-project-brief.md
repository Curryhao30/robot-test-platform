# 面试版项目说明书

**机器人控制器与示教系统自动化验证平台**
Robot Controller & Teach Pendant Validation Platform

> 数据截至 **v0.14.2-teach-pendant-qt-tests / v0.15.0-p3-charts（2026-09-23）**，**91 项自动化用例**，Windows 本机 + GitHub Actions 双端全绿。
> 仓库：https://github.com/Curryhao30/robot-test-platform（public）

---

## 0. 一分钟讲法（先背这个）

**30 秒版：**

> 我面向多轴协作机器人控制器测试开发，做了一个**软硬件解耦的控制器自动化验证平台**。核心链路是"控制指令 → 仿真执行 → 状态采样 → 轨迹判定 → 缺陷回归"。技术上是 **C++ 和 Python 双层**：C++ 承担控制环、运动执行、CiA402 状态机和 EtherCAT 周期交换，保证实时性；Python 只负责测试编排和判定，避免解释器影响实时执行。被测对象是 Robot Profile 参数化的多轴仿真机器人，判定层是独立的 Test Oracle。目前 94 个用例覆盖 PLCopen 运动指令、CiA402 驱动状态机、EtherCAT 虚拟总线、实时性量化、示教器 UI 与协议模拟器、安全联锁仿真，全部自动化跑在 CI 上。

**2 分钟版（面试开场陈述）：**

> 这个项目的出发点很简单：机器人控制器软件是最难测的一类软件——它要验证的不只是"功能对不对"，还有状态机迁移、实时性、总线协议和安全联锁。真机测试成本高、周期长，所以我做了一个软硬件解耦的验证平台。
>
> 架构分四层：**Python 测试编排层**（pytest + Oracle 判定）、**gRPC 协议层**、**C++ Controller Agent**（控制环、PLCopen 状态机、CiA402、EtherCAT 主站）、**被控对象层**（多轴机器人仿真、虚拟从站、虚拟总线，未来可替换为真机 HIL）。
>
> 关键设计有三个：第一，**Python 只下发一次命令，控制周期内的执行和采样全部在 C++ 内完成**，完成后批量回传轨迹和逐周期数据，这样解释器和 RPC 不会影响实时性。第二，**Simulator 负责产生行为，Oracle 负责判定对错**——位置误差、速度钳制、状态机序列合法性都由独立判定层检查。第三，**Robot Profile 参数化**，轴数、限位、限速、控制周期全部走 YAML 配置，换个机型不改测试代码。
>
> 目前已经迭代了 17 个版本 tag，94 个用例全绿，覆盖 CiA402 状态机、EtherCAT 虚拟总线、实时性量化（jitter/latency/overrun）、示教器 Web UI / Qt 桌面面板 / 协议模拟器、总线异常注入、安全联锁仿真。架构上做了 Robot Adapter 抽象，未来接真机只需要改配置不改用例。

---

## 1. 项目背景与目标

### 1.1 背景
- 岗位：机器人控制器/示教器**测试开发**——负责控制器软件、示教器软件、BUG 与版本管理、软件设计文档。
- 痛点：控制器软件验证依赖真机，测试周期长、异常工况（急停、总线断链、越限位）真机复现成本高。
- 解法：仿真层先行，把"控制器行为"与"被测物理对象"解耦，纯软件即可完成状态机、协议、实时性、安全逻辑的自动化验证；真机接入作为 HIL 阶段（接口已预留）。

### 1.2 目标（一句话）
> 以「控制指令 → 仿真执行 → 状态采样 → 轨迹判定 → 协议校验 → 自动报告 → 缺陷回归」为闭环，构建一套面向多轴机器人控制器与示教系统的自动化验证平台。

### 1.3 岗位职责 ↔ 项目映射

| 岗位职责（JD） | 项目对应 | 面试怎么讲 |
|---|---|---|
| 负责控制器的软件开发 | C++ Controller Agent（控制环 / PLCopen 状态机 / 采样） | "我实现了控制器的运动执行与状态机，并用测试反向驱动设计" |
| 负责示教器的软件开发 | 虚拟示教器（五块 UI + REST）+ TP/1.0 示教器协议模拟器 + PyQt5 桌面面板 | "示教器被抽象成客户端，浏览器 UI / 桌面 QT 面板 / 协议模拟器共享同一控制器契约" |
| 管理软件 BUG，管理并发布软件版本 | CI 全绿 + 17 个版本 tag + 测试→缺陷→回归闭环（用例发现 bug 后补回归用例） | "每个里程碑：本地全量绿 → 推 CI → 绿后打 tag，可追溯" |
| 编写软件设计文档 | docs/design.md、docs/p2-design.md、README 项目说明书 | "设计文档覆盖架构、协议、模块、验收、版本记录" |

---

## 2. 技术方法总览（选型即观点）

| 层 | 选型 | 为什么（面试可说的判断） |
|---|---|---|
| 执行层 | **C++17 + CMake + gRPC/Protobuf** | 控制环/状态机/总线交换需要确定性与可控时序，C++ 是行业标准（华沿控制器栈同样 C++） |
| 编排层 | **Python 3.11 + pytest** | 用例组织、数据判定、报告生成效率高；与 C++ 通过 gRPC 解耦 |
| 跨语言 | **gRPC + Protobuf** | 强类型接口契约（.proto 即文档）、天然跨语言；一次命令 + 批量回传符合测试模型 |
| 配置 | **YAML Robot Profile** | 机型参数集中化，测试引擎零改动支持多机型 |
| 示教器 | **FastAPI + 原生 HTML/JS + Playwright + PyQt5 桌面 HMI** | 浏览器 UI 与桌面 QT 面板共享同一 REST 契约；UI 端到端自动化，断言"操作真实改变控制器状态" |
| 协议模拟 | **TCP 行分隔 JSON 帧（TP/1.0）** | 无第三方依赖、帧校验严格、模拟真实示教器协议线 |
| 报告/波形 | **纯 Python / 零依赖 SVG** | CI 与面试机零额外安装（不依赖 matplotlib） |
| 本机工具链 | **MSYS2 clang64 + mingw64 预编译包** | 网络受限环境下唯一可落地路线（vcpkg/GCC 均不可用），详见 §6 |
| CI | **GitHub Actions（ubuntu-latest）** | push/PR 自动构建 + 全量 pytest + 报告产物上传 |

---

## 3. 系统架构

```
┌───────────────────────────────────────────────────────────┐
│ Virtual Teach Pendant（FastAPI + HTML/JS + Playwright）    │  示教器客户端①（REST）
│  Jog / Program / I/O / Robot / Alarm                       │
├───────────────────────────────────────────────────────────┤
│ TP/1.0 协议模拟器（TCP 桥 + 协议客户端，CLI 可独立跑）       │  示教器客户端②（TP 协议线）
│  tp_bridge ↔ tp_simulator                                  │
│ PyQt5 桌面示教器面板（.ui + 业务类，QNetworkAccessManager 调 REST）│  示教器客户端③（QT HMI）
├───────────────────────────────────────────────────────────┤
│ Test Orchestrator（Python/Pytest）                         │  测试编排层
│  Case 调度 · RobotAdapter · Profile · Oracle · 报告/波形   │
├────────────────────── gRPC（一次命令）───────────────────────┤
│ C++ Controller Agent                                      │  执行层（实时/确定性）
│  ControlLoop(cycle_hz) · PLCopen 状态机                   │
│  Cia402StateMachine ×7 · ObjectDictionary                 │
│  VirtualEthercatBus（PDO 交换+实时统计+故障注入）           │
│  SafetyStateMachine（安全联锁，P2.2a）                     │
├───────────────────────────────────────────────────────────┤
│ 被控对象（Plant）                                           │
│  Robot Simulator（梯形速度剖面/同步PTP）                    │
│  VirtualDrive ×7（CiA402 从站仿真）                        │
├───────────────────────────────────────────────────────────┤
│ Test Oracle（判定层）                                       │
│  Position/Velocity/Trajectory/Timing/StateMachine         │
│  CiA402 迁移合法性 · 总线故障语义 · 安全联锁                │
└───────────────────────────────────────────────────────────┘
```

### 3.1 实时性边界（项目核心论断，面试必讲）
- **Python 只下发一次命令**（MoveAbsolute / RunCycles），不参与控制周期；
- **C++ 内部控制环**：`while(running){ update_motion(); sample_position(); sample_timestamp(); }`；
- 控制周期与数据采样均在 C++ Agent 内完成，完成后**批量回传** timestamp[]/joint[]/逐周期数组；
- 逐周期明细（interval_us / processing_us）由 C++ 侧记录，Python 侧只做统计与绘制。

> 面试原句：**"控制周期和数据采样均在 C++ Agent 内完成，Python 仅负责测试编排和结果分析，避免解释器和 RPC 调度影响实时执行。"**

---

## 4. 核心模块详解（每个：干什么 / 怎么测 / 面试怎么说）

### 4.1 C++ Controller Agent（controller-agent/）
- **干什么**：控制环（可配置 500/1000/2000Hz）、PLCopen 运动状态机（IDLE/BUSY/ACTIVE/DONE/ABORTED/ERROR + DISABLED/ENABLED/FAULT）、运动命令执行（Enable/Disable/Home/MoveAbsolute/MoveRelative/Stop/Reset/GetState）、轨迹采样与缓冲。
- **怎么测**：P0 10+1 用例——正常执行、运动中 Stop、新命令抢占、越限位、未使能、急停→Reset 恢复、Reset 后重新运动。
- **实现细节**：类结构 `ControllerAgent / RobotModel / Axis / AxisGroup / MotionExecutor /
  TrajectoryGenerator / StateMachine / DataRecorder`；gRPC 服务四组（Controller /
  Cia402 / Ethercat / Safety）；控制环按 `profile.cycle_hz`（500/1000/2000Hz）自旋对齐
  周期；PLCopen 运动状态 IDLE/BUSY/ACTIVE/DONE/ABORTED/ERROR + 控制器级
  DISABLED/ENABLED/FAULT；采样线程把 timestamp/关节位置/速度/加速度/状态写入缓冲，
  指令完成后批量回传（一次 gRPC 响应带全轨迹，见 3.1 实时性边界）。
- **面试怎么说**："状态机语义对标 PLCopen：Busy/Active/Done、CommandAborted、Error+ErrorID；急停是 FAULT 级，必须 Reset 才能恢复——这是真实控制器语义，不是玩具。"

### 4.2 Robot Simulator（simulator/）
- **干什么**：RobotModel（各轴状态+限位/限速）、TrajectoryGenerator（梯形速度剖面、同步 PTP 按最慢轴统一时长）、MotionExecutor（按周期推进规划）。
- **边界**：P0 用关节空间梯形剖面验证**运动行为与状态机**；FK/IK、TCP 轨迹、MoveLin/MoveCirc 属后续阶段，不混入。
- **实现细节**：`TrajectoryGenerator` 生成梯形速度剖面（加速-匀速-减速三段，参数
  velocity/acceleration/deceleration/jerk），同步 PTP 按"最慢轴统一时长"规划——
  所有轴同时启停，避免关节间异步；`MotionExecutor` 按控制周期推进规划并钳制限位/限速；
  越限位在模型层触发 Error，为 Joint Limit Oracle 提供真实输入。
- **面试怎么说**："第一版刻意不做 7 轴 DH 正逆解——控制器测试岗位要验证的是运动执行和状态机，不是证明能推逆运动学。梯形剖面 + 同步 PTP 足够支撑 PLCopen 指令与 Oracle 判定，运动学留到后续。"

### 4.3 Robot Profile（robot_profiles/maira_sim.yaml）
- **干什么**：配置中心——轴数、控制周期、各轴限位/限速/加减速、精度阈值、接口、驱动 Profile。
- **面试怎么说**："不写死 `AXIS_NUM=7 / CONTROL_FREQ=2000 / ERROR=0.01`，而是 YAML 配置：`controller.cycle_hz`、`joint_limits.position[]`、`accuracy.repeatability_mm`。华沿不同系列规格不同（Elfin-Pro 最高 2000Hz、S 系列 1000Hz、MAiRA 重复定位 ±0.01mm、Elfin-Pro ±0.02~0.05mm），参数化是行业常识。当前 P0 用 `maira_sim`（MAiRA-like 仿真配置，**明确标注非官方内部参数**），换机型只加 YAML。"

### 4.4 Test Oracle（orchestrator/oracle/）
- **干什么**：独立判定层——position（|q_final−q_target|≤ε）、velocity（max|dq/dt|≤Vmax）、trajectory（无突变/收敛）、timing、state_machine（状态序列合法性）、cia402（迁移合法性/状态字位）、bus_fault（隔离/断链/恢复）。
- **面试怎么说**："整个项目最重要的设计理念：**Simulator 负责产生行为，Oracle 负责判定行为对不对**。测 MoveAbsolute 不是'机器人动了'，而是判定最终位置误差、速度是否越限、状态序列是否 Busy→Done 合法——这和真实控制器测试工程师的工作一致。"

### 4.5 CiA402 驱动状态机 + 对象字典 + 虚拟从站（P1-1）
- **干什么**：完整迁移表 `NotReadyToSwitchOn→SwitchOnDisabled→ReadyToSwitchOn→SwitchedOn→OperationEnabled`，Fault→FaultReset 上升沿恢复；对象字典 0x6040/0x6041/0x6060/0x6064/0x607A；7 个独立虚拟从站。
- **怎么测**：12 用例——状态字位语义（bit0 ready/bit1 switched on/bit2 op enabled/bit3 fault/bit6 switch on disabled）、非法迁移拒绝、FaultReset 上升沿、多从站一致性。
- **实现细节**：状态迁移表覆盖 `NotReadyToSwitchOn → SwitchOnDisabled → ReadyToSwitchOn →
  SwitchedOn → OperationEnabled`，含 QuickStop 与 Fault 分支；控制字 0x6040 位语义
  （bit0 switch on / bit1 enable voltage / bit2 quick stop / bit7 fault reset 上升沿）、
  状态字 0x6041（bit0 ready / bit1 switched on / bit2 operation enabled / bit3 fault /
  bit6 switch on disabled）；对象字典 0x6060 模式、0x6064 实际位置、0x607A 目标位置；
  7 个虚拟从站实例相互独立（用例间 autouse teardown 复位，防污染）。
- **面试怎么说**："CiA402 是 EtherCAT/CoE 上的驱动器 Profile 标准，不是现场总线。我把状态迁移表和对象字典做成可测试的组件——面试官一问我就能画迁移图，而不是只会念协议名。"

### 4.6 EtherCAT 主站抽象：Virtual / SOEM 骨架（P1-2/3/5 + P2.1a）
- **干什么**：`EthercatMaster` 虚接口（exchange/run_cycles/inject_fault/mode），`VirtualEthercatBus`（仿真实现：周期 PDO 交换 + 实时统计 + 故障注入）与 `SoemEthercatBus`（真机骨架：参数校验 + Linux raw socket 网卡探测 + Windows 明确不支持）各自实现；`main.cpp --bus virtual|soem` 一行切换。
- **实现细节**：`VirtualEthercatBus` 周期 PDO 交换——controlword→statusword、状态名透传、
  目标位置 0x607A→0x6064 直通；`RunCycles` 记录逐周期 interval_us（周期间隔）与
  processing_us（单周期交换处理耗时）；故障注入三种语义：LINK_LOSS 整线失败、
  SLAVE_LOSS 指定从站丢失且其余从站照常交换、BUS_ERROR 帧级错误；
  `SoemEthercatBus` 为真机骨架：--bus 切换 + Linux raw socket 网卡探测 + Windows 明确不支持。
- **面试怎么说**："真机接入点钉死在 EthercatMaster 抽象上——P2.1b 只需在 SoemEthercatBus::init() 里替换成 SOEM 的 ecx_init + 从站扫描，上层 gRPC 和 94 个用例零改动；而且骨架现在就保证无网卡/非 Linux 时报清晰错误，不冒充真机已连接。"

### 4.7 实时性量化（P1-3）
- **干什么**：RunCycles 返回逐周期 `interval_us[]`（周期间隔）与 `processing_us[]`（单周期交换耗时），统计 min/max/mean/std + overrun。
- **怎么测**：断言 500Hz（周期 2000us）下 overrun==0 且 latency_max<2000us；1000Hz 同理。
- **实测（本机 2026-09）**：**500Hz jitter max≈87us、latency max≈3us、overrun=0；1000Hz 7 从站 latency max≈6us、overrun=0**。
- **实现细节**：C++ 侧自旋等待到名义周期起点再进入下一周期（busy-wait，不 sleep 累计
  漂移）；overrun 计数记录"实际进入周期晚于名义时刻"的次数；Python 侧拿到逐周期数组后
  **重算校验统计自洽**（max(interval)−jitter_max≈名义周期），防止 C++ 侧统计与原始数据不一致。
- **面试怎么说**："'能跑'不是指标，'确定性'才是。jitter/latency/overrun 三个维度量化控制周期质量，波形图把逐周期抖动画出来——面试官看到波形比看到'测试通过'信服得多。"

### 4.8 总线异常注入（P1-5）
- **干什么**：InjectBusFault/ClearBusFault——LINK_LOSS（整线失败）、SLAVE_LOSS（指定从站丢失隔离，其余照常）、BUS_ERROR（帧级错误）。
- **怎么测**：6 用例——丢失隔离/恢复、断链阻断交换与 RunCycles、注入-清除循环、非法参数拒绝、用例间从站状态隔离（autouse teardown 复位）。
- **面试怎么说**："异常注入是控制器测试的核心手段——真机上模拟总线断链成本极高，仿真里一条 RPC 就能注入，且语义严格区分'丢一个从站'和'整线断'。"

### 4.9 虚拟示教器 + Playwright（P1-4）
- **干什么**：FastAPI 后端 + 原生 HTML/JS 五块 tab UI（Jog/Program/I/O/Robot/Alarm），操作经 REST 桥接**真实控制器状态**。
- **怎么测**：8 项 Playwright 端到端——**Servo ON 真实使能控制器**、**点 Jog+ → 控制器关节 +5°（UI 与 gRPC 双重断言）**、运动中 STOP 实际停止、未使能 Jog → Alarm 联动。
- **面试怎么说**："UI 测试不验证'按钮点没点成功'，而是验证**UI 操作有没有真正改变 Controller State**——这是示教器×控制器联合测试的语义。分辨率兼容也有依据：华沿 Elfin 标准示教器 11" 1920×1200，MAiRA 10.5" 1280×800。"

### 4.10 示教器协议模拟器 TP/1.0（P2.3）
- **干什么**：自研行分隔 JSON 帧协议（非华沿私有协议，明确标注）——`protocol.py` 编解码（坏帧拒绝/粘包半包重组）、`tp_bridge.py` TCP 协议桥（TP 帧 ↔ RobotAdapter）、`tp_simulator.py` 协议客户端（CLI 可独立跑 jog_demo 脚本）。
- **怎么测**：9 用例——编解码往返/坏帧/粘包半包（单测）+ SERVO/JOG/STOP **经协议线真实改变控制器状态**（与 UI 用例同款断言）+ 未知动词拒绝 + 控制器错误透传 + STATE 推送。
- **实现细节**：帧协议为行分隔 JSON（`{"verb": "...", "params": {...}}\n`），坏帧直接
  拒绝并回 NAK；TCP 流按行缓冲做粘包/半包重组（半包等待续传、多帧逐条处理）；动词表
  SERVO/JOG/STOP/HOME/MOVE/STATE；控制器错误码经协议线原样透传给客户端。
- **面试怎么说**："示教器在架构里是'客户端'——浏览器 UI 走 REST、协议模拟器走 TP 协议线，**两条线共享同一控制器契约**。将来华沿协议拿到后，RealTPBridge 只需把厂商报文映射到这套契约，UI 和用例零改动。"

### 4.11 安全联锁仿真（P2.2a）
- **干什么**：SafetyStateMachine（输入 ESTOP/安全门/抱闸/驱动器故障 → 输出 允许使能/停机/抱闸请求），联锁优先级 **ESTOP > 门 > 驱动器故障**；SafetyService 三 RPC（GetSafetyState/SetSafetyInput 注入）；`stop_required` 上升沿联动控制器（ESTOP→急停 FAULT、门开→Guard Stop）。
- **怎么测**：7 用例——ESTOP 联动（注入后控制器 FAULT + 运动被拒）、优先级、门开禁使能、驱动器故障、抱闸防坠落、恢复。
- **边界**：只验证安全逻辑与联锁语义；真实安全等级/FSoE I/O 属 P2.2b HIL，**不越界声称**。
- **面试怎么说**："仿真层明确不声称验证了真实安全等级——安全等级需要认证资质。我验证的是**控制逻辑的联锁行为**：急停必须最高优先、门开必须 Guard Stop、Fault 后必须安全复位。这个边界感很重要。"

### 4.12 Robot Adapter（P2.0）
- **干什么**：`RobotAdapter` 抽象 + `GrpcAdapter` 实现；profile.adapter.type（simulation|hil）一行切换；HIL 缺 endpoint 报错、不可达抛清晰错误。
- **怎么测**：6 用例——配置解析、非法类型拒绝、HIL 缺端点、不可达错误、用例层不出现 ControllerClient。
- **面试怎么说**："P0~P2.3 全部用例只依赖 RobotAdapter 接口——换真机是改配置不改用例。这是'软硬件解耦'的落点，不是口号。"

### 4.13 报告与波形（P1-6）
- 每次 pytest 会话自动生成 `reports/run_YYYYMMDD_NNN/`：report.html / result.json / trajectory.csv / log.txt / waveforms/*.svg（零依赖 SVG，jitter vs 名义周期参考线、latency overrun 红线+超标高亮）。

### 4.13 示教器 Qt 桌面面板与契约测试（P2.3 路径 B 的第三种形态 + 测试补全）
- **干什么**：`teach_pendant/qt_panel.py` PyQt5 桌面示教器（`.ui` + pyuic + 业务类
  `QTTeachPendant`），与 Web 示教器共用同一控制器契约；`RestBackend`（REST）/
  `MockBackend`（离线仿真）双后端，jog 按住连发、HOME 回零、Esc 急停、F11 全屏、
  "测试与曲线"标签页内嵌管理控制台。`MockBackend` 抽为独立模块
  `teach_pendant/mock_backend.py`（qt_panel 与测试共用，避免测试引入 QtWebEngine）。
- **怎么测**：`test_qt_panel.py` 5 项契约测试——使能门禁（未使能 jog/home/move 拒绝）、
  jog 步长增量（正/负/自定义倍率）、回零全 0、绝对运动整体写入、复位清报警与 alarm
  派生；用 QCoreApplication.processEvents() 驱动 QTimer 异步回调，**无 GUI / agent**。
- **面试怎么说**："示教器我做了三种客户端形态：Web、TP 协议桥、Qt 桌面面板；Qt 面板
  与 Web 共用同一控制器契约，离线仿真后端独立成模块后被直接拿去做契约测试——
  测的是示教器软件和控制器之间的行为契约，不是按钮点击。"

### 4.14 管理控制台（P3 → P3-ops → P3-charts：可操作 + 运行数据曲线）
- **干什么**：`orchestrator/app/console.py` 提供 REST API——只读：`/api/cases` 用例清单（AST 扫描）、`/api/runs` 运行历史、`/api/runs/{id}` 运行明细（含 has_trajectory）、`/api/runs/{id}/trajectory`（7 轴轨迹抽稀采样）、`/api/runs/{id}/waveform`（逐周期 jitter/latency JSON）、`/api/waveforms/{name}`（全局波形 SVG）、`/api/defects` 缺陷视图、`/api/summary` 汇总；操作：`POST /api/runs` 触发后台 pytest（全量或按 11 个分组之一，RTP_WRITE_REPORT=1 落盘新 run）、`GET /api/runs/status` 进度轮询、`POST /api/defects/{id}/close|reopen` 人工闭环。`frontend/index.html` 零依赖单页：运行按钮（分组下拉）、运行中徽章 + 3s 轮询、运行行点击展开逐用例明细 + **7 轴轨迹曲线（位置/速度/加速度切换，trajectory.csv 抽稀 400 点 SVG 折线）** + 逐周期 jitter/latency 波形、实时性波形面板（全局 SVG）、缺陷关闭/重开（MANUAL 标记）。**运行中实时曲线（v0.16.0-p3-realtime）**：adapter 包装自动捕获逐用例轨迹段（对用例零侵入），每用例结束 flush 到 reports/active.live.jsonl；`/api/runs/active/stream` SSE 端点增量推送；前端 EventSource 边跑边刷新 7 轴曲线（运行累计时间轴）；运行结束归档 run_dir/active.jsonl 并清理 live 文件作为 SSE 结束信号——数据全部来自 C++ Agent 批量采样。
- **怎么测**：`test_console.py` 12 项——清单结构、运行历史结构、自动闭环语义、汇总一致性、run 级逐周期波形 JSON、触发运行、运行中状态、分组/未知分组 400、并发 409、运行明细 404、轨迹曲线 API（抽稀结构/404）、波形 SVG 端点（200/404/路径穿越防护）。
- **工程细节**：曲线全部由真实运行产物驱动——trajectory.csv（C++ Agent 逐周期采样）抽稀后返 JSON，前端零依赖 SVG 折线；run 级 waveform.json 与全局 waveforms/*.svg 双通道；后台运行 subprocess 隔离 + agent 运行时 PATH 补齐（RTP_MSYS_ROOT 可覆盖）；缺陷人工状态以用例名为稳定 key（BUG 序号随轮次漂移）。
- **面试怎么说**："管理侧不只管用例和缺陷，还直接展示运行过程数据：点开一次真实运行的明细，能看到 7 轴位置/速度/加速度轨迹曲线和逐周期 jitter/latency 波形——这些曲线全部来自 C++ Agent 的周期采样和测试产物，不是模拟数据，验证结果和过程数据是一体的。"

### 4.15 PyQt5 桌面示教器面板（示教器客户端③，补齐 QT 缺口）
- **干什么**：`teach_pendant/qt_panel.py`（业务类）+ `qt_panel.ui`（Qt Designer UI）+ `ui_qt_panel.py`（pyuic5 生成）。用 `QNetworkAccessManager`（QT 原生异步网络栈）调 teach_pendant REST（`/api/servo`、`/api/jog`、`/api/status`、`/api/home`、`/api/alarm`、`/api/move_absolute`），是示教器的**桌面 HMI** 第三客户端，与浏览器 UI、TP/1.0 协议模拟器共享同一控制器契约。
- **工程结构（贴近工业 Qt Creator 工作流）**：`qt_panel.ui`（设计）→ `pyuic5 qt_panel.ui -o ui_qt_panel.py`（生成 UI 类，勿手改）→ `qt_panel.py`（业务类 `QTTeachPendant(QMainWindow, Ui_QtTeachPendant)`，加载 UI + 信号连接 + 控制逻辑）。改 UI 只重跑 pyuic5，业务代码零触碰。
- **功能**：7 关节实时角度读数（500ms 轮询）、每关节 +/− 增量 jog、Servo ON/OFF、Stop、Reset、**Home 回零**、Move Absolute、报警面板、操作日志、状态 LED。
- **测试与曲线标签页（桌面端聚合网页端数据）**：面板用 `QTabWidget` 分出第二页「测试与曲线」，内嵌 `QWebEngineView` 直接加载管理控制台单页（`frontend/index.html`），**复用其用例清单 / 运行历史 / 缺陷视图 / 逐周期 jitter·latency 数据曲线（SVG）**，零重写、保真度最高。控制台由 `app/console.py`（FastAPI）托管，只读查看无需控制器 agent；曲线来自 `reports/run_*/waveform.json`（全量或"波形报告"分组运行产出）。
- **怎么跑通这一页**：先起控制台 `py -m uvicorn app.console:app --port 58090`（或一键 `py teach_pendant/run_real_backend.py` 同时拉 agent+示教器后端+控制台），再用 `py teach_pendant/qt_panel.py --url http://127.0.0.1:58081 --console-url http://127.0.0.1:58090`；切到「测试与曲线」页即可看到与浏览器端一致的测试和曲线。
- **两种运行模式**：真实模式（先起 `uvicorn teach_pendant.main:app`，连控制器 gRPC）；`--mock` 离线模式（内置 `MockBackend` 仿真控制器，无需 agent 即可演示，直接给面试官看 jog/回零/报警）。
- **怎么验证**：`py_compile` + 无头（`QT_QPA_PLATFORM=offscreen`）构建窗口；`--mock` 下 servo→jog→home 关节回零逻辑通过。PyQt5 仅面板依赖，**不进 pytest/CI**（避免 GUI 环境耦合，保持 83 用例全绿）。
- **面试怎么说**："岗位要求 QT/HMI，我补了一个 PyQt5 桌面示教器面板——Qt Designer 画 UI、pyuic 生成代码、业务类接管逻辑，是工业 Qt 标准分工；网络走 QT 原生 QNetworkAccessManager 而非裸 socket，一看就是正经 QT 工程结构，不是脚本玩具。"
- **截图/录屏指引（简历素材）**：
  1. 离线演示最快：`python -m teach_pendant.qt_panel --mock` → 点 **Servo ON** → 各关节 `+/−` 按钮使能 → 连点 J0 `+` 看读数实时 +5° 递增 → 点 **Home** 全部回零。
  2. 截图点：① 使能后状态 LED 变绿 + 关节读数；② jog 后某关节非零；③ Home 后全部归零且 Log 出现 `home: {...}`。
  3. 录屏（OBS/ShareX，15~30s）：Servo ON → J2 连点 + → Home 回零，旁白"这是 PyQt5 写的示教器桌面端，复用同一套控制器 REST 契约"。
  4. 真实模式（有 agent 时）：先 `python -m uvicorn teach_pendant.main:app --port 58081`，再 `python -m teach_pendant.qt_panel --url http://127.0.0.1:58081`，操作会真实改变控制器关节状态（与 Playwright 用例同语义）。
  5. 测试与曲线页（演示用）：另开 `python -m uvicorn app.console:app --port 58090`，桌面面板切到「测试与曲线」标签页即内嵌控制台网页——点运行历史任意一行展开，能看到逐用例 PASS/FAIL 明细与 jitter/latency 两条数据曲线；这一页直接把"网页端看的测试和曲线"搬到了桌面 HMI，简历素材里可强调"QT 面板聚合了测试报告与波形"。

### 4.16 运行中实时曲线（P3-realtime，v0.16.0）
- **干什么**：把"运行完成后回看曲线"升级为“**运行过程中边跑边刷新的实时曲线**”
  （示波器/在线监控语义）——SSE 流式推送 + 前端 EventSource 动态绘制。
- **链路**：`_wrap_adapter` 包装 RobotAdapter，每次运动指令成功后自动捕获一段轨迹
  （`Trajectory.from_result` 抽稀 ≤200 点，**对用例零侵入**）→ 存入
  `capture.TRAJECTORY_SEGMENTS` → 每用例结束 conftest flush 到
  `reports/active.live.jsonl`（追加）→ console 新增 `/api/runs/active/stream` SSE 端点
  增量推送 → 前端实时轨迹面板（EventSource）按"运行累计时间轴"动态滚动绘制 7 轴曲线。
- **结束信号**：运行结束 console 清 `_ACTIVE_RUN` 并**删除 live 文件**；SSE 生成器
  连续无新数据且 is_active() 为 False 即收尾（`event-stream` 正常断开，前端自动关流）；
  同时 live 内容归档到 `run_dir/active.jsonl`（历史 run 可回看）。
- **数据真实性**：全部来自 C++ Agent 批量采样（真实 1000Hz 采样抽稀），无模拟数据；
  段内时间相对化（t−t₀），前端按段累计成连续运行时间轴。
- **怎么测**：3 项——生成器"已有段推送 + 运行中追加 + 结束后 StopIteration"、
  无数据且无运行快速收尾、HTTP 层 event-stream 完整响应（data 事件计数）。
- **面试怎么说**："'能跑'是结果，**运行中的过程数据可视化**是测试平台该有的能力：
  曲线边跑边滚，全部来自真实采样；结束信号不是轮询猜的，是运行生命周期的自然产物
  （live 文件清理）。"

---

## 5. 测试体系（94 项）

| 模块 | 用例数 | 文件 | 关键覆盖 |
|---|---|---|---|
| P0 运动控制核心 | 11 | test_p0_core.py | 使能/回零/绝对定位/位置精度/速度钳制/限位/Stop/未使能/急停/抢占/Reset 重运动 |
| CiA402 状态机 | 12 | test_cia402.py | 全迁移表/状态字位/对象字典/FaultReset 上升沿/非法迁移/多从站 |
| 虚拟 EtherCAT 总线 | 7 | test_ethercat.py | 总线信息/PDO 周期交换/RunCycles/高频率稳定性 |
| 实时性量化 | （并入 ethercat） | | 500/1000Hz overrun==0 + latency 阈值 |
| 虚拟示教器 UI | 8 | test_teach_pendant.py | 五块 tab/Servo ON 真实使能/Jog+→关节+5°/Stop/Alarm 联动 |
| 总线异常注入 | 6 | test_bus_fault.py | 丢失隔离/断链阻断/错误恢复/参数拒绝/状态隔离 |
| 波形可视化 | 2 | test_waveform.py | 数组长度/统计自洽/SVG 生成 |
| Robot Adapter | 6 | test_adapter.py | 配置切换/非法类型/缺端点/不可达 |
| SOEM 骨架 | 3 | test_p2_soem.py | 无网卡 FATAL/可操作提示/非法 --bus |
| 安全联锁 | 7 | test_safety.py | ESTOP 联动/优先级/门/故障/抱闸/恢复 |
| 示教器协议模拟器 | 9 | test_tp_protocol.py | 编解码/坏帧/粘包/SERVO/JOG/STOP/错误透传/推送 |
| 管理控制台 | 15 | test_console.py | 清单/历史/自动闭环/汇总 + 逐周期波形 JSON + 触发运行/状态轮询/并发拒绝/明细/人工闭环 + 轨迹曲线 API/波形 SVG 端点 + 实时曲线 SSE 生成器语义（增量推送/结束收尾/HTTP event-stream） |
| Qt 示教器面板 | 5 | test_qt_panel.py | MockBackend 契约：使能门禁 / jog 步长增量 / 回零 / 绝对运动 / 复位与报警派生（无 GUI/agent） |

**设计要点**：实时性断言由逐周期明细重算校验；bus_fault 用例 autouse teardown 复位从站防污染；safety 用例 teardown 清输入 + Reset 防污染共享 agent；波形自洽（max(interval)−jitter_max≈名义周期）。

---

## 6. 工程化与演进（17 个 tag）

### 6.1 里程碑时间线
| tag | 内容 | CI |
|---|---|---|
| v0.1.0-p0 | P0 全链路：pytest→gRPC→C++ Agent→Simulator→Oracle→报告 | ✅ |
| v0.2.0-p1 | CiA402 状态机 + 对象字典 + 7 虚拟从站 | ✅ |
| v0.3.0-p1 | 虚拟 EtherCAT 总线（PDO 周期交换 + RunCycles） | ✅ |
| v0.4.0-p1 | 实时性量化：jitter/latency/overrun | ✅ |
| v0.5.0-p1 | 虚拟示教器（五块 UI）+ Playwright | ✅ |
| v0.6.0-p1 | 总线异常注入（LINK/SLAVE/BUS + 恢复） | ✅ |
| v0.7.0-p1 | 逐周期波形可视化（SVG） | ✅ |
| v0.8.0-p2-plan | P2 规划基线（Adapter/HIL/FSoE/示教器） | ✅ |
| v0.9.0-p2.0 | Robot Adapter 抽象：simulation/hil 切换，用例零改动 | ✅ |
| v0.10.0-p2.1a | EthercatMaster 抽象 + SOEM 骨架：--bus 切换，无网卡清晰 FATAL | ✅ |
| v0.11.0-p2.2a | SafetyService 安全联锁仿真 + 控制器联动 | ✅ |
| v0.12.0-p2.3 | 示教器协议模拟器 TP/1.0 | ✅ |
| v0.13.0-p3 | 管理控制台（console 只读 API + 单页前端 + 报告写盘门控） | ✅ |
| v0.14.0-p3-ops | 可操作控制台：触发运行/分组、进度轮询、运行明细、缺陷人工关闭·重开 | ✅ |
| v0.15.0-p3-charts | 运行数据曲线：7 轴轨迹（位置/速度/加速度）+ 逐周期 jitter/latency 波形 + 全局波形面板 | ✅ |
| v0.14.2-teach-pendant-qt-tests | 示教器 Qt 面板测试补全：MockBackend 抽离独立模块 + 5 项契约测试 | ✅ |
| v0.16.0-p3-realtime | 运行中实时曲线：逐用例轨迹段自动捕获 + SSE 流式推送 + 前端 EventSource 边跑边刷新 | ✅ |

**节奏纪律**（面试重点讲）：每个里程碑 = **本地全量绿 → 推 CI → CI 绿后打 tag**，17 个 tag 全部可追溯。

### 6.2 问题与踩坑档案（现象 → 根因 → 解决 → 固化，体现工程深度）

> 面试策略：不用背全部，但**每个问题都能讲出"现象—根因—解决—固化"四要素**，
> 这是比"我做过 XX"可信得多的信号。按阶段组织如下。

#### A. 跨平台编译与 CI（gRPC / Protobuf / 端口）

| # | 现象 | 根因 | 解决 | 固化 |
|---|---|---|---|---|
| A1 | CI 编译报 `cannot convert grpc::ServerBuilder to const int`（main.cpp AddListeningPort 处） | Ubuntu apt gRPC≈1.51 是 **fluent API**：`AddListeningPort` 返回 `ServerBuilder&`，端口经第 3 参 `selected_port` 输出；本机 mingw64 gRPC 1.82 语义不同，"selected==0 判失败"在 Linux 恒假/恒 0 | 读 gRPC 版本行为差异：改用第 3 参 `selected_port` 判定 + 不依赖返回值；记录到设计文档 | design.md CI 排障链 |
| A2 | `CMake Error: Could not find a package configuration file provided by "Protobuf"` | Ubuntu `libprotobuf-dev` 不提供 `ProtobufConfig.cmake`，`find_package(Protobuf)` 失败 | CMake 双轨：apt 头文件 + 手动定位 grpc_cpp_plugin / protoc；配置期失败落盘日志并随报告上传（诊断用） | CI workflow 配置期诊断步骤 |
| A3 | pytest 全部报 `controller_agent 未就绪`（10 errors，agent 日志却显示 Listening 0.0.0.0:xxxx） | 启动竞态链：等待超时太短 / 日志缓冲未刷（stdout 未 flush）/ wait_ready 失败没记 last_err / 端口被占用导致 FATAL 退出 | 综合修复：MAX_SPAWN_ATTEMPTS=5 重试 + 候选端口轮换 + wait_ready 记录 last_err + 失败时日志落盘 reports/agent.log 上传 artifact 供诊断 | conftest spawn 逻辑 |
| A4 | 本机/CI 偶发 `WSA10048` / 端口绑定失败 | 动态端口被 Hyper-V 排除区间占用；CI 偶发端口冲突 | 固定候选端口列表 `[50051,50551,51051,51551,52051,52551]` 按序尝试，绑定失败自动换下个 | CANDIDATE_PORTS |
| A5 | 注解 `Node.js 20 is deprecated ... actions/checkout@v4` 被迫跑在 Node 24 | GitHub Actions 弃用 Node20 | 关注并升级 actions 到 Node24 版本（v4→v5 等），当前为 warning 不阻塞 | CI 持续维护 |
| A6 | 注解 `ubuntu-latest 将迁移 Ubuntu 26 (2026-10-19)` | GitHub Actions runner 镜像滚动更新 | 保持追踪，必要时锁 `ubuntu-24.04` 消除不确定性 | CI 持续维护 |
| A6 | 通知 `ubuntu-latest 将迁移 Ubuntu 26 (2026-10-19)` | runner 镜像滚动更新 | 保持追踪，必要时锁 `ubuntu-24.04` | CI 持续维护 |

#### B. 本机工具链（Windows，网络受限环境）

| # | 现象 | 根因 | 解决 | 固化 |
|---|---|---|---|---|
| B1 | vcpkg / gRPC 源码无法下载 | 本机网络受限 | MSYS2 预编译包（mingw64/clang64）落地 gRPC/Protobuf | README 工具链章节 |
| B2 | 本机 GCC 16.2 启动即崩溃（0xc0000135） | MSYS2 GCC 与运行时 DLL 不匹配 | 改用 clang++ + `-stdlib=libstdc++` 对齐 mingw64 ABI；`--unwindlib=libgcc` 避免双解卷器冲突 | CMake toolchain 说明 |
| B3 | 中文路径导致工具链异常 | 路径含中文（C:\Users\jh\测试开发） | subst 映射短盘符 R: | 构建脚本 |
| B4 | 本机全量 pytest 突然全红（agent 0xC0000135 起不来） | **本 PowerShell 进程 PATH 缺 msys64**：agent 依赖 gRPC/absl DLL | 运行前 `$env:PATH` 前缀补 `msys64\mingw64\bin;msys64\usr\bin`；console 子进程用 `_subprocess_env()` 补 PATH（`RTP_MSYS_ROOT` 可覆盖） | run_tests / console._subprocess_env |
| B5 | PowerShell 内联 heredoc 吞转义，patch 脚本执行异常 | shell 转义层过多 | **先写临时 .py 文件再执行**（如 patch_*.py），不用命令行内联 Python | 工程脚本惯例 |

#### C. 控制器逻辑 bug（测试平台核心价值——异常注入发现真实缺陷）

| # | 现象 | 根因 | 解决 | 固化 |
|---|---|---|---|---|
| C1 | 急停 Reset 后第一次运动被立即急停打断 | `reset()` 未清 `emergency_requested_` 标志 | P2.2a 安全联锁测试暴露（P0 阶段急停测试复位后没有继续运动，发现不了）；修复后补回归用例 | **test_emergency_reset_allows_relaunch**（P0 第 11 项） |

#### D. 后端 / API 与前端（P3 控制台）

| # | 现象 | 根因 | 解决 | 固化 |
|---|---|---|---|---|
| D1 | FastAPI 返回 `(dict, 404)` 被编成 JSON 数组 | FastAPI 对 tuple 返回的隐式处理 | 显式 `JSONResponse(status_code=404)` | console.py 全部错误分支 |
| D2 | `RuntimeError: The starlette.testclient module requires the httpx2 package` | 新版 starlette TestClient 依赖 httpx2 | 依赖清单加 httpx2 | orchestrator requirements |
| D3 | 缺陷人工状态错位：关闭 A 后 B 也被标记 | BUG 序号随运行轮次漂移（同用例不同 run 序号不同） | 人工状态以**用例名**为稳定 key 存 defect_state.json；前端显示 MANUAL 覆盖标记 | console._load_defect_state |
| D4 | 点击展开明细后重进缓存分支不重画曲线 | detail 缓存命中直接 innerHTML，曲线容器没重新加载 | `hasTraj[runId]` 标记 + 缓存命中也调 loadTrajectory + try/catch | frontend toggleDetail |
| D5 | `/api/waveforms/{name}` 可能被路径穿越 | 未过滤文件名 | 白名单校验（.svg 结尾 + 禁 / \ .） | console.api_waveform |
| D6 | 实时曲线 SSE 连接后"卡住"或提前断 | 结束信号不明确：live 文件可能从未创建（无轨迹用例）或已归档 | 生成器三态：文件存在→增量推送；无数据且 is_active→等待；无数据且 !is_active→收尾；live 删除为结束信号；run 结束归档 active.jsonl | _active_stream_gen + 3 项测试 |
| D7 | 实时曲线时间轴从 2.9e8ms 起（agent 绝对时钟） | 段内时间戳是 agent 单调时钟绝对值 | 段内时间相对化（t−t₀），前端按段累计 | conftest._capture_segment |

---

## 7. 高频追问应答（Q&A，最重要的一节）
---

## 7. 高频追问应答（Q&A，最重要的一节）

### A. 架构与实时性

**Q1：为什么 Python + C++ 双层，不直接用 C++ 或纯 Python？**
> Python 写测试用例、做判定、出报告效率高，但解释器和 RPC 调度无法保证 0.5ms 级确定性；C++ 承担控制环和采样。**分工原则：Python 只下发一次命令，控制周期内的执行与采样全在 C++ 侧，完成后批量回传。** 如果让 Python 每周期调一次 gRPC 拿位置，这个架构面试时一定会被问倒，所以设计上直接避开。

**Q2：gRPC 为什么不用 HTTP/REST？**
> 跨语言强类型契约、.proto 即接口文档、天然支持 streaming 和批量消息；控制器指令模型是"一次命令 + 批量回传"，gRPC 的 message 语义最贴合。示教器 UI 走 REST 是因为浏览器场景，协议层用 gRPC，两个层面职责不同。

**Q3：jitter 是怎么测的？数据可信吗？**
> C++ 侧 RunCycles 真实计时：记录每个周期的实际间隔 interval_us 和处理耗时 processing_us，自旋等待到名义周期再进入下一周期，统计 min/max/mean/std 和 overrun 次数；Python 拿到逐周期数组后重算校验统计自洽。实测 500Hz jitter max≈87us、latency max≈3us、overrun=0。要说明的是这是**仿真环境指标**，真机验收以 HIL 阶段数据为准——这一点我会主动讲，面试官反而信任。

**Q4：为什么不直接在真机上测？**
> 真机是最终验收（P2.1b HIL），但开发期真机成本高、异常工况难复现。仿真层让状态机、协议、实时性逻辑先行验证，Robot Adapter 抽象保证切真机只改配置。而且**仿真发现的问题一样是控制器实现的 bug**（比如 reset 急停标志残留），不是仿真器的问题。

### B. 协议栈

**Q5：CiA402 是什么？和 EtherCAT 什么关系？**
> CiA402 是驱动器设备 Profile（状态机 + 对象字典），不是总线协议；EtherCAT 是总线，CiA402 通过 CoE 跑在 EtherCAT 上。架构里体现层级：**EtherCAT → CoE → CiA402**。我实现了 9 状态迁移表 + 0x6040/0x6041 控制字/状态字 + 对象字典读写 + FaultReset 上升沿，7 个从站相互独立。

**Q6：SOEM 骨架为什么在 Windows 上直接报不支持？**
> SOEM 依赖 Linux raw socket（AF_PACKET）做实时帧交换，Windows 上要么用 WinPcap 要么用商业栈，且 Windows 定时器粒度不满足硬实时。骨架阶段明确报错比"假装能跑"诚实——真机 HIL 部署目标是 Linux 工控机，这个决策本身就是懂行。

**Q7：EtherCAT 虚拟总线和真实从站区别？**
> 虚拟总线仿真 PDO 周期交换、实时统计和故障注入语义；真实从站要处理扫描、拓扑、DC 同步。抽象层把两者统一到 EthercatMaster 接口，真机接入只替换实现。**虚拟总线验证的是控制器侧逻辑，不声称等价于真实总线物理特性。**

**Q8：Modbus/FSoE/CAN 这些华沿接口为什么不都做？**
> 华沿 MAiRA 控制箱接口有 EtherCAT/FSoE、TCP/IP、CAN、Modbus。全部实现没有意义——**优先做岗位含金量最高、且能验证深度的 EtherCAT/CiA402 链路**；Modbus 属于预留接口，FSoE 安全走 P2.2b 真实安全 I/O。选型要证明深度，不是罗列广度。

### C. 测试方法

**Q9：测试数据哪来的？没有真机数据怎么跑测试？**
> 测试数据是**产物不是前提**——控制器是确定性系统，指令（目标位置、速度、限位）由用例定义，行为由 Simulator 产生，判定由 Oracle 基于 Profile 阈值完成。这不叫"没有数据"，这叫**指令驱动测试**：期望值来自规格（限速、限位、精度阈值），实际值来自被控对象回传，比较即得结论。

**Q10：Test Oracle 和断言的区别？**
> 断言是"单个结果对不对"，Oracle 是**独立的判定组件**，可复用、可组合：位置误差、速度约束、加速度、状态序列合法性、总线故障语义都是独立判定器。Simulator 产生行为、Oracle 判定对错，两者解耦后测试逻辑才能被复用和审计。

**Q11：UI 测试（Playwright）为什么不用图像识别？**
> 控件/API 自动化为主，图像识别只做视觉回归兜底。示教器 UI 测试的核心是**验证 UI 操作真实改变 Controller State**（点 Jog+ 后读 gRPC 关节位置），不是验证像素。OpenCV 做 UI 主路径是玩具，业界主路径是 DOM/API 自动化。

**Q12：怎么设计异常测试？**
> 四个层次：状态异常（急停、Fault、越限位）、总线异常（断链、从站丢失、帧错误）、时序异常（重复触发、运动中抢占）、安全异常（急停联动、门开 Guard Stop）。每个异常都要验证"控制器进入正确状态 + 拒绝非法后续操作 + 可恢复路径"。

### D. 工程与排障

**Q13：CI 上遇到过最难的 bug？**
> gRPC 版本差异：Ubuntu apt 的 gRPC≈1.51 是 fluent API，`AddListeningPort` 返回 ServerBuilder& 而不是端口号，用"selected==0 判绑定失败"在 mingw64 1.82 恒 0——本机正常、CI 崩溃，最后通过读 gRPC 版本行为差异解决，并固化进设计文档。这说明了跨平台验证的价值。

**Q14：测试发现过控制器实现 bug 吗？**
> 有。安全联锁联动急停后，`reset()` 没清 `emergency_requested_` 标志，导致复位后第一次运动被立即急停打断——这个 bug 在 P0 阶段不可能被发现（P0 急停测试复位后没有继续运动），是 P2.2a 异常注入把它暴露的。修复后补了回归用例。**这就是测试平台的核心价值：用异常注入发现隐藏缺陷。**

**Q15：版本管理怎么做的？**
> Conventional Commits + 语义化 tag；每个里程碑"本地全量绿 → 推 CI → 绿后打 tag"；CI 在 push/PR 自动构建 + 全量 pytest + 上传报告产物。测试→缺陷→回归闭环：用例失败 → 定位 → 修复 → 补回归用例 → 重跑全量。

**Q16：为什么不用现成的禅道/Jira 管 BUG？**
> 那个是管理平台，我要证明的是**控制器测试开发能力**。我建立了 Requirement→TestCase→TestRun→Defect→Build→Release 的可追溯关系（测试发现 bug → 定位版本 → 回归验证），这是测试工程的核心，而不是再做一套工单系统。

### E. 行业与华沿

**Q17：为什么 Robot Profile 参数化？华沿产品你了解吗？**
> 华沿不同系列规格不同：Elfin-Pro 控制周期最高 2000Hz、S 系列 1000Hz；MAiRA 重复定位精度 ±0.01mm，Elfin-Pro 约 ±0.02~±0.05mm；MAiRA 7 自由度、控制箱接口 EtherCAT/FSoE、TCP/IP、CAN、Modbus；示教器 Elfin 11" 1920×1200、MAiRA 10.5" 1280×800。所以写死参数是错的，配置化是行业常识。当前用 `maira_sim`（MAiRA-like 仿真配置），不冒充官方内部参数。

**Q18：MAiRA 的 7 轴为什么没做完整逆运动学？**
> 控制器测试岗位验证的是指令执行、状态机、实时性和协议，不是运动学算法本身。7 自由度冗余 IK 是研究级问题，做进测试平台会让项目失控。关节空间梯形剖面 + 同步 PTP 已支撑全部 PLCopen 指令测试；FK/IK、TCP 轨迹判定是独立后续阶段。

**Q19：安全功能为什么不声称验证了？**
> 安全等级（PL/SIL）需要认证资质与真实安全 I/O。我验证的是**控制逻辑的联锁行为**（急停优先级、门 Guard Stop、Fault 复位），仿真层明确标注边界，HIL 阶段才验证真实 I/O。诚实声明边界比夸大安全结论专业得多。

**Q20：这个项目简历上怎么定位？**
> 一句话：**"机器人控制器与示教系统自动化测试平台｜C++ / Python / Linux / EtherCAT / CiA402 / PLCopen"**——多轴软硬件解耦测试架构、C++ 控制环与 Python 编排双层、Robot Profile 参数化、Simulator+Oracle 判定、CiA402/EtherCAT/示教器/安全联锁自动化验证，94 用例 CI 全绿，17 个版本里程碑。

**Q21：后续规划？**
> P2.1b 真 EtherCAT HIL（SOEM 接入真机，jitter/latency 变真机验收指标）、P2.2b 真实安全 I/O、RealTPBridge 真示教器（需厂商协议资料）；运动学 FK/IK + MoveLin/MoveCirc 路径判定；管理侧 Requirement→TestCase→TestRun→Defect→Build→Release 闭环。

**Q22：项目里最自豪的设计？**
> 三个：① **实时性边界**——Python 只下发一次命令，控制周期全在 C++ 内，批量回传；② **Simulator 产生行为 / Oracle 判定对错**的架构；③ **异常注入发现真实 bug**（reset 急停标志残留）——证明这套平台对控制器软件有实际测试价值。

**Q23：一个人做全栈，怎么保证质量？**
> 分层验证：协议层有编解码单测，服务层有集成测试，UI 有端到端，实时性有量化指标；CI 双端全绿 + 报告产物留痕；每个里程碑打 tag 可追溯。个人项目尤其要靠自动化测试兜底——这正是这个岗位的核心能力。

**Q24：和华沿岗位的契合点？**
> 岗位要控制器软件、示教器软件、版本管理、设计文档——项目四者全覆盖；技术栈 C++/Python/Linux/gRPC 对齐；行业语义（PLCopen/CiA402/EtherCAT/示教器/安全联锁）是本项目的骨架而不是名词堆砌；异常注入与回归闭环直接对应测试开发职责。

**Q25：如果让你真机接一台 Elfin-Pro，第一步做什么？**
> 先建 `huayan_elfin_pro_public` Profile（公开规格：轴数、周期、限位、精度），用现有 94 用例在 Simulation 模式全量跑通基线；再 P2.1b 把 SOEM 接到真实 EtherCAT 从站（工控机 Linux + 伺服），跑 test_ethercat 核心用例对齐从站数；最后 jitter/latency/overrun 以真机实测进报告。每一步都有明确验收，不改用例只改配置。

**Q26：实时曲线是怎么做的？数据从哪来？**
> 运行中逐用例轨迹段由 **adapter 包装自动捕获**——每次运动指令成功后把 C++ Agent 批量回传的采样抽稀成段（≤200 点），对用例代码零侵入；每用例结束 flush 到 `active.live.jsonl`，console 用 **SSE** 增量推送，前端 EventSource 动态滚动绘制。数据 100% 来自真实采样，不是模拟。面试官如果问"运行中 Python 怎么拿逐周期数据"——答：不拿。**高频数据仍在 C++ 侧，Python 拿的是用例粒度批量结果**，实时曲线只是把这个真实结果按用例粒度流式展示，架构论断没被破坏。

**Q27：为什么用 SSE 不用 WebSocket？**
> 实时曲线是**单向推送**（服务端→前端），SSE 足够且更简单：HTTP 语义、自动断线重连、无需双向握手；WebSocket 适合需要实时上行交互的场景（比如示教器 jog 遥操作，那是另一条线，走 REST 已经够）。选型看通信方向，不是看哪个"新"。

**Q28：实时曲线怎么知道运行结束了？**
> 运行生命周期驱动：console 的后台 pytest 子进程退出时清 `_ACTIVE_RUN` 并**删除 live 文件**（结束信号）；SSE 生成器连续无新数据且无运行即收尾，前端 onerror 到 CLOSED 自动关流；历史数据已归档到 `run_dir/active.jsonl` 可回看。不是靠前端猜超时。

---

## 8. 华沿产品情报与技术表述对照（避免说错）

| 话题 | 正确表述 | 常见错误 |
|---|---|---|
| 控制周期 | Elfin-Pro 最高 2000Hz；S 系列 1000Hz | "统一 2000Hz" |
| 精度 | MAiRA 重复定位 ±0.01mm；Elfin-Pro 约 ±0.02~±0.05mm；**重复定位精度 ≠ 轨迹精度** | "轨迹精度 ±0.01mm" |
| 总线 | EtherCAT（+FSoE 安全）、TCP/IP、CAN、Modbus | 把 EtherCAT 与 CiA402 并列（不同层级） |
| CiA402 | 驱动器 Profile：状态机 + 对象字典，跑在 CoE 上 | "CiA402 总线协议" |
| PLCopen | 运动控制功能块/接口规范（MC_MoveAbsolute 等） | "PLCopen 现场总线" |
| 示教器 | Elfin 11" 1920×1200；MAiRA 10.5" 1280×800 | 型号张冠李戴 |
| 自由度 | MAiRA 7 轴 | "华沿全是 7 轴"（Elfin 系列不同） |

---

## 9. 简历压缩版

**项目一行版：**
> 机器人控制器与示教系统自动化测试平台｜C++ / Python / Linux / EtherCAT / CiA402 / PLCopen

**简历 300 字版：**
> 设计多轴机器人控制器软硬件解耦测试架构，开发 C++ Controller Agent（控制环 + PLCopen 状态机）与 Python/Pytest 测试编排引擎，实现 CiA402 驱动状态机、EtherCAT 虚拟总线、Modbus/实时性量化及异常工况自动验证；构建 Robot Profile 参数化多轴仿真与 Test Oracle，对关节位置、速度钳制、状态机序列、控制周期 jitter/latency/overrun 自动判定；实现虚拟示教器（UI + Playwright）与 TP/1.0 示教器协议模拟器、安全联锁仿真（ESTOP>门>驱动器故障 + 控制器联动）；Robot Adapter 抽象支持 Simulation/HIL 一行切换。94 项用例 CI 全绿，17 个版本里程碑，测试→缺陷→回归闭环。

---

## 10. 面试红线（不要说的话）

1. ❌ "我做了个机器人仿真网页" → ✅ "我做了运动控制软件自动化验证框架"
2. ❌ "控制周期统一 2000Hz / 精度统一 ±0.01" → ✅ "按机型 Profile 参数化"
3. ❌ "Python + pysoem 测 2000Hz 实时性" → ✅ "Python 编排，C++ 控制环与采样"
4. ❌ "OpenCV 图像识别做 UI 主路径" → ✅ "控件/API 自动化为主，图像只做兜底"
5. ❌ "我验证了急停安全等级" → ✅ "验证了安全逻辑与状态联锁语义，安全等级需 HIL 与认证"
6. ❌ "CiA402 是 EtherCAT 协议" → ✅ "CiA402 是驱动器 Profile，CoE 跑在 EtherCAT 上"
7. ❌ "这个数据是华沿内部参数" → ✅ "基于公开规格的 MAiRA-like 仿真配置"
8. ❌ "AI 缺陷分诊" → 项目明确不做，别主动提（对控制器测试岗位价值低）
