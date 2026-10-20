@echo off
echo === 示教器桌面面板启动器 ===
if not exist "R:\.venv\Scripts\python.exe" (
  subst R: "C:\Users\jh\测试开发\robot-test-platform"
)
cd /d "R:\orchestrator"
echo 正在启动 Qt 面板（真实联动模式，连 58081 后端 + 58090 控制台）...
echo 若窗口未弹出，下方会显示错误信息。
"R:\.venv\Scripts\python.exe" -m teach_pendant.qt_panel --url http://127.0.0.1:58081 --console-url http://127.0.0.1:58090
echo.
echo 面板已退出，请查看上方错误信息。
pause
