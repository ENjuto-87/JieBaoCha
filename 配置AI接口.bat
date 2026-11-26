@echo off
setlocal
set "CONTRACT_REVIEW_LAUNCH_MODE=ai"
if defined CONTRACT_REVIEW_CONSOLE_READY goto run
set "CONTRACT_REVIEW_CONSOLE_READY=1"
"%ComSpec%" /d /k call "%~f0" %*
exit /b
:run
if not exist "%~dp0launch_demo.bat" goto missing
call "%~dp0launch_demo.bat" %*
exit /b %errorlevel%
:missing
echo ERROR: launch_demo.bat is missing. Extract the complete ZIP before running.
exit /b 1
