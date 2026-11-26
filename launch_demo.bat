@echo off
setlocal
chcp 65001 >nul
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
if not defined CONTRACT_REVIEW_LAUNCH_MODE set "CONTRACT_REVIEW_LAUNCH_MODE=check"
pushd "%~dp0"
if errorlevel 1 goto badfolder
if not exist "launch_demo.py" goto missingentry
set "_CR_PYTHON="
if not defined CONTRACT_REVIEW_PYTHON goto autodetect
if not exist "%CONTRACT_REVIEW_PYTHON%" goto badpython
set "_CR_PYTHON=%CONTRACT_REVIEW_PYTHON%"
goto python
:autodetect
if exist ".venv\Scripts\python.exe" set "_CR_PYTHON=%CD%\.venv\Scripts\python.exe"
if not defined _CR_PYTHON if exist "venv\Scripts\python.exe" set "_CR_PYTHON=%CD%\venv\Scripts\python.exe"
if not defined _CR_PYTHON if exist "..\.venv\Scripts\python.exe" set "_CR_PYTHON=%CD%\..\.venv\Scripts\python.exe"
if not defined _CR_PYTHON if exist "..\venv\Scripts\python.exe" set "_CR_PYTHON=%CD%\..\venv\Scripts\python.exe"
if defined _CR_PYTHON goto python
"%SystemRoot%\System32\where.exe" py >nul 2>nul
if not errorlevel 1 goto pylauncher
"%SystemRoot%\System32\where.exe" python >nul 2>nul
if not errorlevel 1 goto systempython
echo ERROR: Python was not found. Install Python 3.12 or set CONTRACT_REVIEW_PYTHON.
goto failed
:python
"%_CR_PYTHON%" "launch_demo.py" "%CONTRACT_REVIEW_LAUNCH_MODE%" %*
goto done
:pylauncher
py -3 "launch_demo.py" "%CONTRACT_REVIEW_LAUNCH_MODE%" %*
goto done
:systempython
python "launch_demo.py" "%CONTRACT_REVIEW_LAUNCH_MODE%" %*
goto done
:badpython
echo ERROR: CONTRACT_REVIEW_PYTHON does not point to an existing python.exe.
goto failed
:missingentry
echo ERROR: launch_demo.py is missing. Extract the complete ZIP before running.
goto failed
:failed
set "_CR_EXIT=1"
goto finish
:done
set "_CR_EXIT=%errorlevel%"
:finish
if not "%_CR_EXIT%"=="0" echo ERROR: Program exited with code %_CR_EXIT%. Read the details above.
popd
exit /b %_CR_EXIT%
:badfolder
echo ERROR: Cannot access the project directory.
exit /b 1
