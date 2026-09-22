# P2 规划：真实 EtherCAT HIL / FSoE 安全联锁 / 真示教器

> 文档编号：RTP-DESIGN-P2-001 ｜ 版本：v0.8（规划基线）｜ 状态：**规划中，未实现**
> 前置：P0（v0.1.0-p0）+ P1（v0.2.0-p1 ~ v0.7.0-p1）已完成，48 用例双端全绿。
> 本文档回答三个问题：**怎么接真机不破坏现有测试？安全联锁怎么从仿真走到 HIL？
> 真示教器怎么进？**——以及各自的验收标准与风险。

## 1. 目标与范围

P2 的目标是**把 P0/P1 的"软硬件解耦"论断兑现到真实硬件**：同一套 48 个用例，
在把底层被控对象从"仿真/虚拟"换成"真实 EtherCAT 伺服 + 真实示教器"后，
**用例代码零改动**即可继续运行。

三块工作按依赖顺序：

| 里程碑 | 内容 | 依赖 | 硬件 |
|--------|------|------|------|
| P2.0 | **Robot Adapter 抽象层**（配置化切换 Simulation/HIL） | 无 | 无（纯软件） |
| P2.1 | **真 EtherCAT HIL**：SOEM 主站替换虚拟总线 | P2.0 | 1× 工控机/树莓派 + 1× 伺服驱动器（如支持 CiA402） |
| P2.2 | **FSoE 安全联锁**：安全逻辑仿真 → 真实安全 I/O | P2.1 | 安全 PLC/安全继电器或 FSoE 安全端子 |
| P2.3 | **真示教器接入**：真实示教器协议桥 | P2.1 | 华沿/第三方示教器（协议需厂商资料，见 §5） |

## 2. 总体架构演进（P1 → P2）

```
            P1（现状）                              P2（目标）
┌───────────────────────────┐      ┌───────────────────────────────┐
│ pytest ──> ControllerClient │      │ pytest ──> ControllerClient    │  ← 用例零改动
└─────────────┬─────────────┘      └───────────────┬───────────────┘
              │ gRPC                                │ gRPC
┌─────────────▼─────────────┐      ┌───────────────▼───────────────┐
│ C++ Controller Agent       │      │ C++ Controller Agent           │
│  ControlLoop / PLCopen     │      │  ControlLoop / PLCopen         │
│  Cia402StateMachine        │      │  Cia402StateMachine            │
│  VirtualEthercatBus        │      │  EthercatMaster  ←抽象接口     │
└─────────────┬─────────────┘      └───────────────┬───────────────┘
              │ (被控对象)                           │ (被控对象)
┌─────────────▼─────────────┐      ┌───────────────▼───────────────┐
│ VirtualDrive ×7（软件）    │      │  VirtualEthercatBus（仿真）     │
└───────────────────────────┘      │  SoemEthercatBus（真机，SOEM）  │
                                    │  SafetyManager（联锁）          │
                                    └───────────────────────────────┘
```

**核心论断（写进简历/面试）**：协议层（proto）与用例层（pytest）不感知底层是
虚拟还是真实从站——因为 **EthercatService 本身就是抽象**：BusInfo / CycleExchange /
RunCycles / InjectBusFault 在真机上语义等价（真机上有实际 watchdog / WKC / 周期抖动），
P2 只是给这个抽象换一个实现。

## 3. P2.0 Robot Adapter 抽象层（接口预留，纯软件）

### 3.1 为什么先做它
现在测试通过 `ControllerClient` 直连 C++ Agent（唯一入口，已天然可替换）。
P2.0 把这一层显式化，让"切真机"变成**改配置，不改用例**。

### 3.2 设计
```
orchestrator/app/
├── client.py          # 现状：gRPC 客户端（保持不动，作为 HIL 的 client 实现）
└── adapters/
    ├── __init__.py    # get_adapter(profile) -> RobotAdapter
    ├── base.py        # RobotAdapter 协议：enable/stop/move_absolute/run_cycles/...
    └── grpc_adapter.py# 现状 client 的适配器包装（Simulation/HIL 共用）
```

- `RobotProfile` 新增：
  ```yaml
  adapter:
    type: simulation            # simulation | hil
    # hil 时：
    #   endpoint: "192.168.1.10:50051"   # HIL 盒子 gRPC 地址
    #   ethercat: { master_iface: "eth0", slaves: 7, cycle_hz: 1000 }
  ```
- 测试入口（conftest）改为：读 Profile → `get_adapter(profile)` → 用例层只依赖
  `RobotAdapter` 协议。**48 个用例的 import 从 `client` 改为 `adapters`，行为不变**。

### 3.3 验收
1. 全量 48 用例经 adapter 层仍全绿（CI）；
2. `adapter.type: hil` + 无硬件时，报清晰错误（"HIL 未配置/未连接"），不挂死；
3. 用例文件不出现 `ControllerClient` 直连（仅 adapter 内允许）。

## 4. P2.1 真 EtherCAT HIL（SOEM 主站替换虚拟总线）

### 4.1 替换路径
C++ Agent 内部：`VirtualEthercatBus` 与 `SoemEthercatBus` 实现同一抽象：

```cpp
class EthercatMaster {            // 新抽象（simulator/include/robottest/ethercat_master.hpp）
 public:
  virtual bool exchange(const std::vector<PdoOutput>&, std::vector<PdoInput>&,
                        std::string& err) = 0;
  virtual bool run_cycles(const std::vector<PdoOutput>&, int cycles, int cycle_hz,
                          CycleStats&, std::vector<PdoInput>&, std::vector<PdoInput>&,
                          std::string& err) = 0;
  virtual bool inject_fault(BusFault, int slave_id) = 0;  // 真机：WKC 检查/断线模拟
  ...
};
```

- `VirtualEthercatBus`：现有实现，直接继承该抽象（改动极小）；
- `SoemEthercatBus`：用 **SOEM**（Simple Open EtherCAT Master，Linux 原生）实现
  PDO 交换与周期统计；`cycle_hz` 用 clock_nanosleep 定时；latency/jitter 统计复用
  CycleStats（**P1-3 的实时性量化直接变成真机验收指标**）；
- 启动参数：`--ethercat iface=eth0 slaves=7`；无网卡时回退仿真（可配）。

### 4.2 硬件与接线（最小 HIL 台架）
| 件 | 选型 | 用途 |
|----|------|------|
| 主站主机 | 工控机 或 树莓派 4B（Linux，支持实时内核更佳） | 跑 C++ Agent + SOEM |
| 伺服驱动器 | 支持 CiA402 的 EtherCAT 伺服（如 台达/汇川/雷赛 常用款） | 真从站 ×1~2 起步 |
| 电机 | 与驱动器匹配的伺服电机 + 编码器 | 位置环真实闭环 |
| 网卡 | 主站机原生千兆网口（禁 USB 转网卡） | EtherCAT 实时性要求 |
| 电源/刹车 | 驱动器供电 + 使能/抱闸 | 安全第一 |

> 风险标注：真机 HIL 需购买硬件（千元级起步），且调试周期不可控。
> **建议**：先把 P2.0 完成（纯软件、可演示"接口已预留"），硬件到位前
> 用 SOEM 的 **虚拟网卡/环回** 做 CI 级验证；面试时讲"接口预留 + 真机替换路径"
> 已足够证明控制器测试开发能力。

### 4.3 真机上 48 用例的映射
| P1 虚拟语义 | P2 真机语义 |
|------------|-------------|
| VirtualDrive 状态机 | 真伺服 CiA402（SOEM 读 statusword 写 controlword） |
| SLAVE_LOSS | 拔线/WKC 错误：该从站 watchdog 超时（LOST），其余继续 |
| LINK_LOSS | 断网：整线错误，主站检测 link down |
| BUS_ERROR | WKC 错误/帧错误 |
| jitter/latency | 真机调度抖动（主站 OS 实时性）——**最有说服力的真机指标** |

### 4.4 验收
1. 同一份 `test_ethercat.py` 在真机模式下通过（slave 数匹配）；
2. RunCycles 真机 jitter/latency 数据进报告与波形；
3. 断线/恢复用例在真机上可演示（拔线 → LOST → 恢复）。

## 5. P2.2 FSoE 安全联锁

### 5.1 设计原则（延续 P0/P1 的"仿真先行"）
P0 已有急停语义（emergency stop → FAULT）；P1 有故障注入。P2.2 把它升级为
**完整安全联锁矩阵**：先在仿真层把安全逻辑验证到位，再在 HIL 阶段验证真实 I/O——
避免"无真机声称验证了安全等级"的错误表述。

### 5.2 安全状态机与联锁矩阵（仿真层先行）
```
SafetyStateMachine（P2 新增，simulator/）
  输入：ESTOP / 门开关 / 抱闸 / 安全区信号 / 驱动器 Fault
  输出：允许使能 / 立即停机 / 禁止运动 / 请求抱闸

联锁矩阵（示例）：
  条件                              →  结果
  ESTOP 触发                        →  EmergencyStop：立即停 + FAULT + 抱闸
  门开 且 非 STO                  →  禁止使能；运动中 → 停止
  未抱闸释放 且 重力负载            →  禁止使能（防坠落）
  驱动器 Fault                     →  FAULT；Reset 需安全复位
```

- proto 新增 `SafetyService`：`GetSafetyState / SetSafetyInput（仿真注入）/
  SetSafetyOutput（读联锁输出）`；
- 用例：`test_safety.py` —— ESTOP 触发联动（运动中立即停 + 状态机 FAULT +
  抱闸请求）、门开禁使能、Fault 后安全复位、联锁优先级（ESTOP > 门 > 驱动器）。

### 5.3 HIL 阶段（真实安全 I/O）
- 真机安全信号经 **FSoE（FailSafe over EtherCAT，走 CoE 的 Safety 通道）** 或
  硬接线安全继电器接入；C++ 侧 SafetyManager 读真实输入、写真实输出；
- **明确边界**：仿真层只验证"安全逻辑与联锁语义"；HIL 层才验证"真实 I/O 电气
  行为"。任何安全相关结论不得跨越该边界（面试话术同样遵守）。

### 5.4 验收
1. `test_safety.py` 全绿（仿真层，CI 可跑）；
2. HIL 阶段：真实 ESTOP 按下 → 控制器联动行为与仿真一致（演示视频/报告）。

## 6. P2.3 真示教器接入

### 6.1 路径
现状 Virtual Teach Pendant 已把 UI 与 gRPC 解耦（REST 桥）。真示教器接入有两条路：

| 路径 | 说明 | 前提 |
|------|------|------|
| A. 真示教器硬件桥 | 真实示教器（华沿 10.5"/11" 款）经其通信协议接入 REST 层 | **协议通常私有，需厂商技术资料/NDA** |
| B. 示教器协议仿真客户端 | 按公开/厂商协议实现一个示教器协议模拟器，替代浏览器 UI | 协议可得（公开样例或厂商提供） |

### 6.2 接口预留
- `teach_pendant/main.py` 的 REST API（/api/status、/api/jog、/api/program、/api/alarm）
  已是稳定契约：真示教器桥 = 在 REST 下加 `RealTPBridge`（协议 ↔ REST），UI 层不变；
- **风险标注**：华沿示教器协议未公开（官网仅有规格：Elfin 标准示教器 11" 1920×1200；
  MAiRA 10.5" 1280×800）。P2.3 默认走路径 B（协议模拟器）可独立推进；
  路径 A 需要厂商资料，作为待办而非承诺。

### 6.3 验收
1. 路径 B：示教器协议模拟器通过现有 8 项 UI 用例语义（操作 → 控制器状态变化）；
2. 路径 A（若资料可得）：真示教器按键 → gRPC 关节变化 → 报告留痕。

## 7. 验收标准（P2 整体）

1. **用例零改动**：48 用例在 Simulation 与 HIL 两种 adapter 下均可运行
   （HIL 需要匹配的从站数/机型 Profile）；
2. **配置切换**：`profile.adapter.type: simulation|hil` 一行切换，无代码改动；
3. **安全边界**：仿真层结论不越界声明为真实安全验证；
4. **实时性真机指标**：真机 RunCycles jitter/latency/overrun 进报告与波形；
5. 每里程碑完成即打 tag（v0.8.0-p2-plan → v0.9.0-p2.0 → …）。

## 8. 明确不做（P2 范围外）

- AI 缺陷分诊（对控制器测试岗位价值低，保持不做）；
- 自研 EtherCAT 从站协议栈（SOEM/商业栈已有）；
- 声称验证真实安全等级（无认证资质，仅验证控制逻辑联锁行为）；
- 完整机器人运动学（FK/IK/MoveLin/MoveCirc 属独立阶段，不并入 P2 HIL）。

## 9. 里程碑与节奏建议

| 序 | 动作 | 产出 | 预计 |
|----|------|------|------|
| 1 | P2.0 adapter 抽象 + 用例改造 + CI 绿 | v0.9.0-p2.0 | 纯软件，1 刀 |
| 2 | SOEM 抽象（EthercatMaster）+ 虚拟网卡 CI 验证 | v0.10.0-p2.1a | 纯软件 |
| 3 | 真机台架（硬件采购/接线） | — | 硬件依赖 |
| 4 | 真机跑通 test_ethercat 核心用例 | v0.11.0-p2.1b | 硬件依赖 |
| 5 | SafetyService + test_safety（仿真） | v0.12.0-p2.2a | 纯软件 |
| 6 | 真实安全 I/O（若硬件允许） | v0.13.0-p2.2b | 硬件依赖 |
| 7 | 示教器协议模拟器 | v0.14.0-p2.3 | 协议可得 |
