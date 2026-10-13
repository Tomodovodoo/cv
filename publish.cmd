@echo off
rem Build the CV and publish it to https://tomodovodoo.github.io/cv/ (double-click, or run: publish.cmd)
call "%~dp0build.cmd" --publish %*
set "RESULT=%ERRORLEVEL%"
echo %cmdcmdline% | find /i "%~0" >nul && pause
exit /b %RESULT%
