// 虚拟驱动器从站：CiA402 状态机 + CoE 对象字典的组合体。
// 对应真实 EtherCAT 从站驱动器的软件仿真；每轴一个实例，状态相互独立。
#pragma once

#include <cstdint>
#include <string>

#include "robottest/cia402_state_machine.hpp"
#include "robottest/object_dictionary.hpp"

namespace robottest {

class VirtualDrive {
public:
    explicit VirtualDrive(int slave_id = 0);

    // SDO 风格访问
    bool read_object(uint16_t index, uint32_t& value);
    // 写对象：0x6040 桥接状态机；只读对象/未知对象拒绝
    bool write_object(uint16_t index, uint32_t value, std::string& err);

    // CiA402 语义便捷接口
    void set_controlword(uint16_t cw);       // 写 0x6040 -> 状态机迁移
    uint16_t statusword() const;             // 读 0x6041（由状态机实时生成）
    void inject_fault();                     // 测试辅助：故障注入
    std::string state_name() const { return sm_.state_name(); }
    bool in_fault() const { return sm_.in_fault(); }

    // 运行模式（0x6060 / 0x6061）
    bool set_mode(uint8_t mode);
    uint8_t mode() const { return static_cast<uint8_t>(od_.get(Obj::kModesOfOperation)); }

    int slave_id() const { return slave_id_; }

private:
    void sync_statusword();

    int slave_id_;
    Cia402StateMachine sm_;
    ObjectDictionary od_;
};

}  // namespace robottest
