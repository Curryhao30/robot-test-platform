# Robot Simulator：多轴机器人被控对象模型（Plant）
add_library(robot_sim STATIC
  src/trajectory.cpp
)
target_include_directories(robot_sim PUBLIC include)
target_compile_features(robot_sim PUBLIC cxx_std_17)
