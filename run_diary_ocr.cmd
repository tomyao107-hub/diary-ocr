@echo off
setlocal
cd /d "%~dp0"

set "VENV_PYTHON=%CD%\.venv\Scripts\python.exe"
if not exist "%VENV_PYTHON%" (
    echo Creating project virtual environment...
    python -m venv .venv
    if errorlevel 1 goto :error
)

"%VENV_PYTHON%" -c "import PyQt6, PIL, openai, fitz" >nul 2>&1
if errorlevel 1 (
    echo Installing project dependencies...
    "%VENV_PYTHON%" -m pip install -r requirements.txt
    if errorlevel 1 goto :error
)

"%VENV_PYTHON%" "diary_ocr_app.py" %*
if errorlevel 1 goto :error
exit /b 0

:error
echo.
echo Failed to start Diary OCR. Review the error above.
pause
exit /b 1
