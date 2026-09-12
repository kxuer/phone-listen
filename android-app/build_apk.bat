@echo off
setlocal
cd /d "%~dp0"

set "PROJECT=%cd%"
set "PROJECT=%PROJECT:\=/%"

echo [1/2] 构建 Docker 镜像（首次约 5-10 分钟）...
docker build -t phone-listen-builder .
if errorlevel 1 ( echo 构建镜像失败 & pause & exit /b 1 )

echo.
echo [2/2] 编译 APK（首次会下载依赖，请耐心等待）...
docker run --rm -v "%PROJECT%:/project" -w /project phone-listen-builder gradle assembleDebug --no-daemon
if errorlevel 1 ( echo 编译失败 & pause & exit /b 1 )

echo.
echo ========================================
echo APK 生成完成:
echo   app\build\outputs\apk\debug\app-debug.apk
echo ========================================
explorer "app\build\outputs\apk\debug"
pause
