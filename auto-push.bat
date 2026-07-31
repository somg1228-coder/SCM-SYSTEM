@echo off
setlocal EnableExtensions

cd /d "%~dp0"

if /i "%~1"=="--help" goto :usage
if /i "%~1"=="-h" goto :usage
if /i "%~1"=="/?" goto :usage

where git >nul 2>nul
if errorlevel 1 (
  echo [ERROR] git is not installed or not available in PATH.
  pause
  exit /b 1
)

git rev-parse --is-inside-work-tree >nul 2>nul
if errorlevel 1 (
  echo [ERROR] This folder is not a git repository.
  pause
  exit /b 1
)

for /f "usebackq delims=" %%B in (`git branch --show-current`) do set "BRANCH=%%B"
if not defined BRANCH (
  echo [ERROR] Could not detect the current git branch.
  pause
  exit /b 1
)

for /f "usebackq delims=" %%T in (`powershell -NoProfile -Command "Get-Date -Format 'yyyy-MM-dd HH:mm:ss'"`) do set "NOW=%%T"

if "%~1"=="" (
  set "COMMIT_MSG=Auto update %NOW%"
) else (
  set "COMMIT_MSG=%*"
)

echo.
echo === SCM Portal auto push ===
echo Branch: %BRANCH%
echo Commit: %COMMIT_MSG%
echo.

git status --short
echo.

git add -A
if errorlevel 1 (
  echo [ERROR] git add failed.
  pause
  exit /b 1
)

git diff --cached --quiet
if not errorlevel 1 (
  echo No staged changes. Nothing to commit or push.
  pause
  exit /b 0
)

git commit -m "%COMMIT_MSG%"
if errorlevel 1 (
  echo [ERROR] git commit failed.
  pause
  exit /b 1
)

git push -u origin %BRANCH%
if errorlevel 1 (
  echo [ERROR] git push failed. Check GitHub login, network, or branch permission.
  pause
  exit /b 1
)

echo.
echo [OK] Changes pushed to origin/%BRANCH%.
pause
exit /b 0

:usage
echo Usage:
echo   auto-push.bat
echo   auto-push.bat "commit message"
echo.
echo Without a commit message, the script uses "Auto update yyyy-MM-dd HH:mm:ss".
echo It runs: git add -A, git commit, then git push -u origin current-branch.
pause
exit /b 0
