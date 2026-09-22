#include "robottest/virtual_drive.hpp"

namespace robottest {

VirtualDrive::VirtualDrive(int slave_id) : slave_id_(slave_id) {
    sync_statusword();
}

bool VirtualDrive::read_object(uint16_t index, uint32_t& value) {
    if (index == Obj::kStatusword) {
        value = statusword();
        return true;
    }
    if (index == Obj::kModesDisplay) {
        value = mode();
        return true;
    }
    return od_.read(index, value);
}

bool VirtualDrive::write_object(uint16_t index, uint32_t value, std::string& err) {
    if (index == Obj::kControlword) {
        set_controlword(static_cast<uint16_t>(value & 0xFFFF));
        return true;
    }
    if (index == Obj::kModesOfOperation) {
        set_mode(static_cast<uint8_t>(value & 0xFF));
        return true;
    }
    if (!od_.write(index, value)) {
        err = (od_.read(index, value) ? "read-only object"
                                      : "unknown object index");
        return false;
    }
    return true;
}

void VirtualDrive::set_controlword(uint16_t cw) {
    sm_.set_controlword(cw);
    sync_statusword();
}

uint16_t VirtualDrive::statusword() const {
    return sm_.statusword();
}

void VirtualDrive::inject_fault() {
    sm_.inject_fault();
    sm_.update();  // P1：故障反应单周期完成 -> Fault
    sync_statusword();
}

bool VirtualDrive::set_mode(uint8_t mode) {
    od_.set(Obj::kModesOfOperation, mode);
    return true;
}

void VirtualDrive::sync_statusword() {
    od_.set(Obj::kStatusword, sm_.statusword());
}

}  // namespace robottest
