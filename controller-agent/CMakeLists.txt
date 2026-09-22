# C++ Controller Agent：控制环 + PLCopen 状态机 + gRPC 服务
find_package(Protobuf CONFIG REQUIRED)
find_package(gRPC CONFIG REQUIRED)
find_package(yaml-cpp CONFIG REQUIRED)

if(TARGET yaml-cpp::yaml-cpp)
  set(YAML_CPP_TARGET yaml-cpp::yaml-cpp)
else()
  set(YAML_CPP_TARGET yaml-cpp)
endif()

set(PROTO_SRC ${CMAKE_CURRENT_SOURCE_DIR}/../proto/controller.proto)
set(GEN_DIR ${CMAKE_CURRENT_BINARY_DIR}/generated)
file(MAKE_DIRECTORY ${GEN_DIR})
# gRPC C++ 插件：优先取 CMake target（上游 gRPC ≥1.30 导出）；
# Debian/Ubuntu 打包的 gRPC 可能不导出该 target，回退到 find_program（protoc-gen-grpc）
if(TARGET gRPC::grpc_cpp_plugin)
  get_target_property(GRPC_CPP_PLUGIN gRPC::grpc_cpp_plugin LOCATION)
else()
  find_program(GRPC_CPP_PLUGIN NAMES grpc_cpp_plugin protoc-gen-grpc REQUIRED)
endif()

add_custom_command(
  OUTPUT ${GEN_DIR}/controller.pb.cc ${GEN_DIR}/controller.pb.h
         ${GEN_DIR}/controller.grpc.pb.cc ${GEN_DIR}/controller.grpc.pb.h
  COMMAND protobuf::protoc
          -I ${CMAKE_CURRENT_SOURCE_DIR}/../proto
          --cpp_out=${GEN_DIR}
          --grpc_out=${GEN_DIR}
          --plugin=protoc-gen-grpc=${GRPC_CPP_PLUGIN}
          ${PROTO_SRC}
  DEPENDS ${PROTO_SRC}
  COMMENT "Generating gRPC/protobuf C++ stubs"
)

add_executable(controller_agent
  src/main.cpp
  src/controller_agent.cpp
  ${GEN_DIR}/controller.pb.cc
  ${GEN_DIR}/controller.grpc.pb.cc
)
target_include_directories(controller_agent PRIVATE
  include
  ${GEN_DIR}
)
target_link_libraries(controller_agent PRIVATE
  robot_sim
  gRPC::grpc++
  protobuf::libprotobuf
  ${YAML_CPP_TARGET}
)
