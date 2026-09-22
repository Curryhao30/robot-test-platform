#include "robottest/object_dictionary.hpp"

namespace robottest {

ObjectDictionary::ObjectDictionary() {
    // CiA 402 必备 + 常用对象（subindex 0 为简单实现）
    objects_.emplace(Obj::kControlword, ObjectEntry{Obj::kControlword, 0, Access::RW, 16, 0});
    objects_.emplace(Obj::kStatusword, ObjectEntry{Obj::kStatusword, 0, Access::RO, 16, 0});
    objects_.emplace(Obj::kModesOfOperation, ObjectEntry{Obj::kModesOfOperation, 0, Access::RW, 8, 0});
    objects_.emplace(Obj::kModesDisplay, ObjectEntry{Obj::kModesDisplay, 0, Access::RO, 8, 0});
    objects_.emplace(Obj::kPositionActual, ObjectEntry{Obj::kPositionActual, 0, Access::RO, 32, 0});
    objects_.emplace(Obj::kVelocityActual, ObjectEntry{Obj::kVelocityActual, 0, Access::RO, 32, 0});
    objects_.emplace(Obj::kTargetPosition, ObjectEntry{Obj::kTargetPosition, 0, Access::RW, 32, 0});
    objects_.emplace(Obj::kProfileVelocity, ObjectEntry{Obj::kProfileVelocity, 0, Access::RW, 32, 1000});
    objects_.emplace(Obj::kProfileAcceleration, ObjectEntry{Obj::kProfileAcceleration, 0, Access::RW, 32, 1000});
    objects_.emplace(Obj::kProfileDeceleration, ObjectEntry{Obj::kProfileDeceleration, 0, Access::RW, 32, 1000});
    objects_.emplace(Obj::kHomingMethod, ObjectEntry{Obj::kHomingMethod, 0, Access::RW, 8, 0});
}

bool ObjectDictionary::read(uint16_t index, uint32_t& value) const {
    auto it = objects_.find(index);
    if (it == objects_.end()) return false;
    value = it->second.value;
    return true;
}

bool ObjectDictionary::write(uint16_t index, uint32_t value) {
    auto it = objects_.find(index);
    if (it == objects_.end()) return false;
    if (it->second.access != Access::RW) return false;
    if (index == Obj::kControlword) return false;  // 由 VirtualDrive 桥接状态机
    it->second.value = value;
    return true;
}

void ObjectDictionary::set(uint16_t index, uint32_t value) {
    auto it = objects_.find(index);
    if (it == objects_.end()) return;
    it->second.value = value;
}

uint32_t ObjectDictionary::get(uint16_t index) const {
    auto it = objects_.find(index);
    if (it == objects_.end()) return 0;
    return it->second.value;
}

}  // namespace robottest
