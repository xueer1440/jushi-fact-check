@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ========================================
echo    据实 · 发布前事实核查
echo ========================================
echo   电脑打开：http://127.0.0.1:5000
echo   手机打开：http://电脑内网IP:5000
echo             （手机与电脑需连同一个 Wi-Fi）
echo   关闭这个窗口 = 停止服务
echo ========================================
echo.
.venv\Scripts\python.exe app.py
pause
