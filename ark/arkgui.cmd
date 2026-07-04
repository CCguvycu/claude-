@echo off
setlocal
set "ARK_REPO=C:\Users\cull_\claude-"
if not defined ARK_GUI_PORT set "ARK_GUI_PORT=8788"

set "CLAUDE_CODE_USE_OLLAMA=1"
if not defined OLLAMA_BASE_URL set "OLLAMA_BASE_URL=http://localhost:11434"
if not defined OLLAMA_MODEL set "OLLAMA_MODEL=qwen2.5-coder:7b"
if not defined OLLAMA_NUM_CTX set "OLLAMA_NUM_CTX=32768"
if not defined ANTHROPIC_API_KEY set "ANTHROPIC_API_KEY=ollama"

REM ARK operates in the folder you launched arkgui from.
set "ARK_GUI_CWD=%CD%"

echo.
echo   ARK GUI  ::  http://localhost:%ARK_GUI_PORT%
echo   model: %OLLAMA_MODEL%   workspace: %CD%
echo   (a server window will open; close it to stop ARK)
echo.

del "%TEMP%\ark-gui-url.txt" >nul 2>&1
start "ARK GUI server" bun "%ARK_REPO%\ark-gui\server.ts"
"%SystemRoot%\System32\ping.exe" -n 5 127.0.0.1 >nul
set "ARK_URL=http://localhost:%ARK_GUI_PORT%"
if exist "%TEMP%\ark-gui-url.txt" set /p ARK_URL=<"%TEMP%\ark-gui-url.txt"
start "" %ARK_URL%
endlocal
