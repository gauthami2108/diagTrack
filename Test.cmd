@echo off
rem Run DiagTrack automated tests on Windows.
rem Double-click this file or run from command line: Test.cmd [test_name]
setlocal
cd /d "%~dp0"

set "PY=C:\Program Files\DiagTrack\python\python.exe"
if exist "%PY%" (
    "%PY%" app\manage.py test %*
) else (
    where python >nul 2>nul
    if %errorlevel% equ 0 (
        python app\manage.py test %*
    ) else (
        echo Python was not found. Install DiagTrack using Setup.cmd first.
        pause
        exit /b 1
    )
)

if %errorlevel% neq 0 (
    echo.
    echo Tests failed with exit code %errorlevel%.
) else (
    echo.
    echo All tests completed successfully.
)
if not defined DT_UNATTENDED pause
exit /b %errorlevel%
