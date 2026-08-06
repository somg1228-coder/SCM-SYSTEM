@echo off
setlocal

cd /d "%~dp0"

if not exist data mkdir data

set "PYTHON_EXE=%LOCALAPPDATA%\Programs\Python\Python314\python.exe"
if not exist "%PYTHON_EXE%" set "PYTHON_EXE=python"
set "PYTHONDONTWRITEBYTECODE=1"

where git >nul 2>nul
if not errorlevel 1 (
  start "SCM Portal Auto Git Push" /min powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0auto_git_push.ps1"
)

echo Starting SCM Portal on http://localhost:8502
echo Logs:
echo   data\streamlit.out.log
echo   data\streamlit.err.log
echo.

"%PYTHON_EXE%" -m streamlit run app.py ^
  --server.address 0.0.0.0 ^
  --server.port 8502 ^
  --server.headless true ^
  --server.fileWatcherType none ^
  --client.showErrorDetails full ^
  --logger.level debug ^
  1>data\streamlit.out.log ^
  2>data\streamlit.err.log

echo.
echo Streamlit stopped. Check data\streamlit.err.log if the browser shows an error.
pause
