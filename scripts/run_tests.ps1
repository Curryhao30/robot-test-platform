# Windows 一键运行 P0 链路：构建 C++ Agent -> 生成 gRPC 桩 -> 运行 pytest
# 工具链：MSYS2 clang64 clang++ + mingw64 libstdc++/gRPC/protobuf/yaml-cpp
#        （本机 GCC16.2 无法启动，见 docs/design.md「工具链说明」）
# 用法: .\scripts\run_tests.ps1
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot

# 1) venv
$venvPy = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPy)) {
    Write-Host "[env] 创建 venv..."
    py -3.11 -m venv (Join-Path $root ".venv")
    & $venvPy -m pip install -r (Join-Path $root "orchestrator\requirements.txt")
}

# 2) 工具链（本机路径，按需修改）
$msys = "C:\Users\jh\msys64"
$env:PATH = "$msys\clang64\bin;$msys\mingw64\bin;$msys\usr\bin;C:\Windows\System32"
$cmakeExe = "$msys\mingw64\bin\cmake.exe"
$makeExe = "$msys\usr\bin\make.exe"
$toolchain = Join-Path $root "scripts\toolchain-clang-mingw64.cmake"
if (-not (Test-Path $cmakeExe)) { Write-Host "[err] 未找到 cmake: $cmakeExe"; exit 1 }

# 3) 中文路径规避：subst 映射到 ASCII 盘符 R:（MSYS2 make 传参给 protoc 时中文会乱码）
#    若 R: 已被占用，可改用其他未用盘符（同步修改下方 $buildRoot）
$buildRoot = "R:\build"
if (-not (Test-Path "R:\")) {
    subst R: $root
    if ($LASTEXITCODE -ne 0) { Write-Host "[err] subst R: 失败"; exit 1 }
}
if (-not (Test-Path (Join-Path $buildRoot "CMakeCache.txt"))) {
    & $cmakeExe -S "R:/" -B $buildRoot -G "Unix Makefiles" `
        -DCMAKE_BUILD_TYPE=Release `
        "-DCMAKE_MAKE_PROGRAM=$makeExe" `
        "-DCMAKE_TOOLCHAIN_FILE=$toolchain"
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

# 4) 构建 C++ Agent
& $cmakeExe --build $buildRoot --target controller_agent -j 8
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

# 5) 生成 Python gRPC 桩（如 proto 有变更）
& $venvPy (Join-Path $root "scripts\generate_stubs.py")

# 6) 运行 P0 测试（agent 由 conftest 自动拉起，默认找 build/controller-agent/controller_agent.exe）
Push-Location (Join-Path $root "orchestrator")
& $venvPy -m pytest -v
$code = $LASTEXITCODE
Pop-Location
exit $code
