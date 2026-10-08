@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
title ParetoGPU

rem Python 3.10+: the py launcher, else python / python3 (the Microsoft Store stub fails the version check)
set "PY="
for %%C in ("py -3" "python" "python3") do (
    if not defined PY (
        %%~C -c "import sys; sys.exit(sys.version_info[:2].__lt__((3, 10)))" >nul 2>&1 && set "PY=%%~C"
    )
)
if not defined PY goto :nopython

%PY% -m paretogpu.doctor
if errorlevel 2 (
    echo.
    pause
    exit /b 2
)

echo.
echo Запуск интерфейса...  ^(закройте окно или Ctrl+C - остановить^)
%PY% -m paretogpu ui %*
if errorlevel 1 pause
exit /b

:nopython
echo [FAIL] Не найден Python 3.10+.
echo Скачайте Python 3.10+ с https://www.python.org/downloads/ ^(галочка "Add python.exe to PATH"^)
pause
exit /b 2
