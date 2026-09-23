# Robot Simulator：多轴机器人被控对象模型（Plant）
add_library(robot_sim STATIC
  src/trajectory.cpp
  src/cia402_state_machine.cpp
  src/object_dictionary.cpp
  src/virtual_drive.cpp
  src/virtual_ethercat.cpp
  src/soem_ethercat_bus.cpp
)
target_include_directories(robot_sim PUBLIC include)
target_compile_features(robot_sim PUBLIC cxx_std_17)
