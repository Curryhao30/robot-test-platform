// CoE 对象字典（CiA 402 子集）：虚拟从站的 SDO 访问层。
// 支持 16/32 位对象；0x6040 写由 VirtualDrive 桥接状态机，本层仅登记元数据。
#pragma once

#include <cstdint>
#include <string>
#include <unordered_map>

namespace robottest {

// CiA 402 常用对象
namespace Obj {
constexpr uint16_t kControlword = 0x6040;         // RW, u16, 驱动器控制字
constexpr uint16_t kStatusword = 0x6041;          // RO, u16, 驱动器状态字
constexpr uint16_t kModesOfOperation = 0x6060;    // RW, i8, 运行模式
constexpr uint16_t kModesDisplay = 0x6061;        // RO, i8, 当前模式
constexpr uint16_t kPositionActual = 0x6064;      // RO, i32
constexpr uint16_t kVelocityActual = 0x606C;      // RO, i32
constexpr uint16_t kTargetPosition = 0x607A;      // RW, i32
constexpr uint16_t kProfileVelocity = 0x6081;     // RW, u32
constexpr uint16_t kProfileAcceleration = 0x6083; // RW, u32
constexpr uint16_t kProfileDeceleration = 0x6084; // RW, u32
constexpr uint16_t kHomingMethod = 0x6098;        // RW, i8
}  // namespace Obj

// 访问属性
enum class Access : uint8_t { RO = 0, RW = 1 };

struct ObjectEntry {
    uint16_t index;
    uint8_t subindex;   // 本实现固定 subindex 0
    Access access;
    uint8_t width;      // 8 / 16 / 32 位
    uint32_t value;
};

class ObjectDictionary {
public:
    ObjectDictionary();

    // 读对象；不存在返回 false
    bool read(uint16_t index, uint32_t& value) const;
    // 写对象：只读对象返回 false；0x6040 由 VirtualDrive 桥接（本层拒绝）
    bool write(uint16_t index, uint32_t value);
    // 0x6040 特判（是否由状态机桥接）
    static bool is_controlword(uint16_t index) { return index == Obj::kControlword; }

    // 便捷读写（供 VirtualDrive / 测试）
    void set(uint16_t index, uint32_t value);
    uint32_t get(uint16_t index) const;

private:
    std::unordered_map<uint16_t, ObjectEntry> objects_;
};

}  // namespace robottest
