@echo off
rem ============================================================
rem Сборка фронтенда (Vite). Нужно один раз после правок web\src.
rem ============================================================
setlocal
cd /d "%~dp0..\web"
call npm install --no-fund --no-audit
call npm run build
echo.
echo Готово: web\dist раздаётся сервером (run\05_serve.cmd).
endlocal
