@echo off
REM Parse a demo:  parse_demo.bat path\to\match.dem [YourFaceitName]
cd /d "%~dp0"
if "%~1"=="" (echo Usage: parse_demo.bat path\to\match.dem [YourFaceitName] & pause & exit /b 1)
if "%~2"=="" (python parser\parse_demo.py "%~1") else (python parser\parse_demo.py "%~1" --pov "%~2")
pause
