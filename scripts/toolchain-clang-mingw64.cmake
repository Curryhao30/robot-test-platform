# 本机专用工具链：MSYS2 clang64 的 clang++ + MSYS2 mingw64 的 libstdc++/gRPC/protobuf/yaml-cpp
# 背景：本机 GCC 16.2（MSYS2 mingw64）全家无法启动（加载器异常 0xc0000135），
#       但 clang（LLVM 二进制）可正常运行；gRPC 等库为 GCC ABI（libstdc++），
#       故 clang++ 以 -stdlib=libstdc++ + mingw64 include/lib 路径对齐 ABI。
# 网络恢复后仍可切回 MSVC+vcpkg 或 WSL2 Ubuntu 以同一 CMakeLists 构建。

set(CMAKE_C_COMPILER   C:/Users/jh/msys64/clang64/bin/clang.exe)
set(CMAKE_CXX_COMPILER C:/Users/jh/msys64/clang64/bin/clang++.exe)
set(CMAKE_MAKE_PROGRAM C:/Users/jh/msys64/usr/bin/make.exe)

set(MINGW64 C:/Users/jh/msys64/mingw64)
set(_inc_flags
  "-isystem ${MINGW64}/include/c++/16.2.0"
  "-isystem ${MINGW64}/include/c++/16.2.0/x86_64-w64-mingw32"
  "-isystem ${MINGW64}/include/c++/16.2.0/backward"
  "-isystem ${MINGW64}/include"
)
string(JOIN " " _inc_flags ${_inc_flags})

set(CMAKE_C_FLAGS_INIT "${_inc_flags}")
set(CMAKE_CXX_FLAGS_INIT "-stdlib=libstdc++ --unwindlib=libgcc ${_inc_flags}")
set(CMAKE_EXE_LINKER_FLAGS_INIT "-stdlib=libstdc++ -L ${MINGW64}/lib")
set(CMAKE_SHARED_LINKER_FLAGS_INIT "-stdlib=libstdc++ -L ${MINGW64}/lib")

set(CMAKE_PREFIX_PATH ${MINGW64})
set(CMAKE_FIND_ROOT_PATH ${MINGW64})
set(CMAKE_FIND_ROOT_PATH_MODE_PROGRAM NEVER)
set(CMAKE_FIND_ROOT_PATH_MODE_LIBRARY ONLY)
set(CMAKE_FIND_ROOT_PATH_MODE_INCLUDE ONLY)
set(CMAKE_FIND_ROOT_PATH_MODE_PACKAGE ONLY)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
