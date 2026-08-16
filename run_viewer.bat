@echo off
REM Overwatch - CS2 demo review. Double-click this file.
REM
REM Serves the viewer and, if ANTHROPIC_API_KEY is set, the live "ask about
REM this moment" endpoint. Without the key everything still works - you just
REM don't get the ask box. Stdlib only, nothing to install.
cd /d "%~dp0"
echo Starting viewer at http://localhost:8000/viewer/index.html?match=2v5
start "" "http://localhost:8000/viewer/index.html?match=2v5"
python server\app.py --port 8000
