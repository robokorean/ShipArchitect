@echo off
setlocal
cd /d "%~dp0"
where py.exe >nul 2>nul
if not errorlevel 1 (
    py -3.13 -c "import sys" >nul 2>nul
    if not errorlevel 1 (
        py -3.13 -I launch.py %*
        goto result
    )
    py -3.12 -I launch.py %*
) else (
    python -I launch.py %*
)
:result
set "SA_EXIT=%ERRORLEVEL%"
if not "%SA_EXIT%"=="0" (
    echo Startup failed. Check Python 3.12 or 3.13 and startup.log.
    if "%~1"=="" pause
)
exit /b %SA_EXIT%
