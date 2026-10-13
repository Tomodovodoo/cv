@echo off
rem Build the CV from generator\cv.json into latest\. Double-click, or run: build.cmd [--no-archive] [--html-only] [--publish]
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo First run: setting up the Python environment in .venv ...
    python -m venv .venv || goto :failed
    .venv\Scripts\python -m pip install --disable-pip-version-check -q -r generator\requirements.txt || goto :failed
    .venv\Scripts\python -m playwright install chromium || goto :failed
)

.venv\Scripts\python generator\build.py %* || goto :failed
goto :done

:failed
echo.
echo Build FAILED. The previous CV in latest\ was left untouched.
set "BUILD_ERROR=1"

:done
rem Keep the window open when started by double-clicking in Explorer.
echo %cmdcmdline% | find /i "%~0" >nul && pause
exit /b %BUILD_ERROR%
