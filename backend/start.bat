@echo off
echo Starting Backend Server...
python main.py
if %ERRORLEVEL% NEQ 0 (
    echo.
    echo ----------------------------------------
    echo The backend server crashed with an error!
    echo Please read the error above.
    echo ----------------------------------------
    pause
) else (
    echo Server exited normally.
    pause
)
