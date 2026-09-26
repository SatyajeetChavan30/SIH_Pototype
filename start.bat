@echo off
rem StrataSense launcher for Windows: double-click this file.
rem First run: installs what StrataSense needs (a few minutes), then opens the dashboard in your browser.
rem Everything else - building the knowledge base, imports, live feed, settings - is done in the dashboard.
setlocal
cd /d "%~dp0"
title StrataSense
if "%PORT%"=="" set PORT=8000

where py >nul 2>nul && (set "PYBOOT=py -3") || (set "PYBOOT=python")
if not exist ".venv\Scripts\python.exe" (
  echo [stratasense] creating the Python environment...
  %PYBOOT% -m venv .venv || goto :nopython
)
set "PY=.venv\Scripts\python.exe"

"%PY%" -c "import stratasense" >nul 2>nul
if errorlevel 1 (
  echo [stratasense] installing StrataSense ^(first run only, a few minutes^)...
  "%PY%" -m pip install -q --upgrade pip
  "%PY%" -m pip install -q -e "backend[dev,ocr]" || "%PY%" -m pip install -q -e "backend[dev]" || goto :failed
)

if not exist "frontend\dist\index.html" (
  where npm >nul 2>nul || goto :nonode
  echo [stratasense] building the web dashboard ^(first run only^)...
  pushd frontend
  if not exist node_modules call npm install --no-audit --no-fund || (popd & goto :failed)
  call npm run build || (popd & goto :failed)
  popd
)

echo.
echo [stratasense] StrataSense is starting. Your browser opens at http://localhost:%PORT%
echo [stratasense] Keep this window open while you use StrataSense; close it to stop the server.
echo.
"%PY%" -m stratasense.cli start --port %PORT% --open
goto :eof

:nopython
echo.
echo Python 3.10 or newer is needed: install it from https://www.python.org/downloads/ (tick "Add python.exe to PATH"), then double-click start.bat again.
pause
goto :eof

:nonode
echo.
echo Node.js 18 or newer is needed once, to build the dashboard: install it from https://nodejs.org/ then double-click start.bat again.
pause
goto :eof

:failed
echo.
echo Setup did not finish (see the messages above). Check the internet connection and double-click start.bat again.
pause
