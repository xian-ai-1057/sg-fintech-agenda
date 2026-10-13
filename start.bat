@echo off
setlocal
cd /d "%~dp0"
py -3 -c "import sys; raise SystemExit(sys.version_info.__lt__((3,10)))" >nul 2>&1
if not errorlevel 1 goto use_py
python -c "import sys; raise SystemExit(sys.version_info.__lt__((3,10)))" >nul 2>&1
if not errorlevel 1 goto use_python
python3 -c "import sys; raise SystemExit(sys.version_info.__lt__((3,10)))" >nul 2>&1
if not errorlevel 1 goto use_python3
echo Python 3.10 or later was not found.
echo Install Python from https://www.python.org/downloads/ then try again.
echo No software was downloaded or installed.
goto finish
:use_py
py -3 -m team.launch
goto finish
:use_python
python -m team.launch
goto finish
:use_python3
python3 -m team.launch
:finish
echo.
pause
endlocal
