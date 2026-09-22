# Linux/macOS 一键运行 P0 链路（WSL/CI）
# 用法: bash scripts/run_tests.sh
set -e
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

# 1) 环境
VENV_PY="$ROOT/.venv/bin/python"
if [ ! -f "$VENV_PY" ]; then
  python3 -m venv "$ROOT/.venv"
  "$VENV_PY" -m pip install -r orchestrator/requirements.txt
fi

# 2) CMake（系统安装）
command -v cmake >/dev/null || { echo "[err] cmake missing"; exit 1; }

# 3) 构建 C++ Agent（Release）
BUILD="$ROOT/build"
if [ ! -f "$BUILD/CMakeCache.txt" ]; then
  cmake -S "$ROOT" -B "$BUILD" -DCMAKE_BUILD_TYPE=Release
fi
cmake --build "$BUILD" --config Release --target controller_agent -j"$(nproc)"

# 4) 生成 Python gRPC 桩
"$VENV_PY" scripts/generate_stubs.py

# 5) 运行 P0 测试
cd orchestrator
"$VENV_PY" -m pytest
