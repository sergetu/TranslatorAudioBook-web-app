@echo off
rem ============================================================
rem 0) Запуск модели HY-MT1.5-7B (koboldcpp, порт 5003, 8k ctx)
rem    Модель и koboldcpp живут в D:\AI — ресурсы вне проекта.
rem    Параметры взяты из реальных логов прогонов (AI\logs\hymt15_*.log).
rem ============================================================
setlocal
set KOBOLD=D:\AI\koboldcpp.exe
set MODEL=D:\AI\HY-MT1.5-7B-Q4_K_M.gguf

if not exist "%KOBOLD%" (
  echo [!] Не найден %KOBOLD%
  echo     Проверьте путь к koboldcpp.exe в D:\AI (есть koboldcpp-1.116.1.exe).
  exit /b 1
)
if not exist "%MODEL%" (
  echo [!] Не найдена модель %MODEL%
  exit /b 1
)

"%KOBOLD%" --model "%MODEL%" ^
  --host 127.0.0.1 --port 5003 ^
  --contextsize 8192 --gpulayers 32 --threads 5 --flashattention --usemlock --nommap

echo.
echo Модель остановлена.
endlocal
