# 机器人控制器与示教系统自动化验证平台 - 一键启动器
# 双击 start_platform.vbs 静默调用本脚本：检查并拉起 agent(50051) 与 控制台(58090)，然后打开浏览器。
param([switch]$NoBrowser)

$repo = 'C:\Users\jh\测试开发\robot-test-platform'
$py   = Join-Path $repo '.venv\Scripts\python.exe'
$agentExe = Join-Path $repo 'build\controller-agent\controller_agent.exe'
$profile  = Join-Path $repo 'robot_profiles\maira_sim.yaml'
$orch     = Join-Path $repo 'orchestrator'
$log  = Join-Path $env:TEMP 'rtp_run'
New-Item -ItemType Directory -Force $log | Out-Null

# agent 依赖 msys64 运行库，补齐 PATH
$env:PATH = 'C:\Users\jh\msys64\mingw64\bin;C:\Users\jh\msys64\usr\bin;C:\Windows\System32;C:\Windows'

# 1) 控制器 agent : 50051
if (-not (Get-NetTCPConnection -LocalPort 50051 -State Listen -ErrorAction SilentlyContinue)) {
    Start-Process -FilePath $agentExe -ArgumentList '--profile', $profile, '--port', '50051', '--bus', 'virtual' `
        -WindowStyle Hidden -RedirectStandardOutput (Join-Path $log 'agent.log') -RedirectStandardError (Join-Path $log 'agent.err.log')
}

# 2) 管理控制台 : 58090
if (-not (Get-NetTCPConnection -LocalPort 58090 -State Listen -ErrorAction SilentlyContinue)) {
    Start-Process -FilePath $py -ArgumentList '-m', 'uvicorn', 'app.console:app', '--port', '58090' `
        -WorkingDirectory $orch -WindowStyle Hidden -RedirectStandardOutput (Join-Path $log 'console.log') -RedirectStandardError (Join-Path $log 'console.err.log')
}

Start-Sleep -Seconds 3
if (-not $NoBrowser) {
    Start-Process 'http://127.0.0.1:58090/'
}
