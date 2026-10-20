# 示教器 Qt 桌面面板 - 一键启动
# 双击 tp_panel.vbs 调用：确保 R: 短盘符（QtWebEngine 不支持中文路径）→ 起示教器后端(58081) → 弹出面板窗口。
param([switch]$Mock)

$repo = 'C:\Users\jh\测试开发\robot-test-platform'

# 1) 确保 subst R: 映射（会话级，重启后重建）
if (-not (Test-Path 'R:\.venv\Scripts\python.exe')) {
    cmd /c subst R: "$repo"
    Start-Sleep -Seconds 1
}
$py = 'R:\.venv\Scripts\python.exe'
if (-not (Test-Path $py)) { throw 'R: 映射失败，无法定位 python' }

# 2) 真实模式：确保示教器后端 58081（连 50051 agent）
if (-not $Mock) {
    if (-not (Get-NetTCPConnection -LocalPort 58081 -State Listen -ErrorAction SilentlyContinue)) {
        $log = Join-Path $env:TEMP 'rtp_run'
        New-Item -ItemType Directory -Force $log | Out-Null
        $env:RTP_AGENT_TARGET = '127.0.0.1:50051'
        $env:RTP_PROFILE = Join-Path $repo 'robot_profiles\maira_sim.yaml'
        $env:RTP_TP_PORT = '58081'
        Start-Process -FilePath $py -ArgumentList '-m','uvicorn','teach_pendant.main:app','--port','58081' `
            -WorkingDirectory 'R:\orchestrator' -WindowStyle Hidden `
            -RedirectStandardOutput (Join-Path $log 'tp.log') -RedirectStandardError (Join-Path $log 'tp.err.log')
        Start-Sleep -Seconds 3
    }
}

# 3) 弹 Qt 面板（真实 GUI 窗口，显示在你的桌面）
if ($Mock) {
    Start-Process -FilePath $py -ArgumentList '-m','teach_pendant.qt_panel','--mock' -WorkingDirectory 'R:\orchestrator'
} else {
    Start-Process -FilePath $py -ArgumentList '-m','teach_pendant.qt_panel',`
        '--url','http://127.0.0.1:58081','--console-url','http://127.0.0.1:58090' -WorkingDirectory 'R:\orchestrator'
}
