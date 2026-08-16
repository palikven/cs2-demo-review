@echo off
REM Overwatch - CS2 demo review. Double-click this file.
cd /d "%~dp0"
echo Starting viewer at http://localhost:8000/viewer/index.html
start "" http://localhost:8000/viewer/index.html
python -m http.server 8000
