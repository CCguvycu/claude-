@echo off
setlocal
set "ARK_REPO=C:\Users\cull_\claude-"

set "CLAUDE_CODE_USE_OLLAMA=1"
set "CLAUDE_CODE_USE_NATIVE_FILE_SEARCH=1"
if not defined OLLAMA_BASE_URL set "OLLAMA_BASE_URL=http://localhost:11434"
if not defined OLLAMA_MODEL set "OLLAMA_MODEL=qwen2.5-coder:7b"
if not defined OLLAMA_NUM_CTX set "OLLAMA_NUM_CTX=32768"
if not defined ANTHROPIC_API_KEY set "ANTHROPIC_API_KEY=ollama"
set "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1"
set "DISABLE_TELEMETRY=1"
set "DISABLE_ERROR_REPORTING=1"
set "DISABLE_AUTOUPDATER=1"

set "ARK_PERSONA=%ARK_REPO%\ark-persona.md"

REM Skip the banner in print mode so piped output stays clean.
set "ARK_PRINT="
for %%A in (%*) do (
  if /I "%%~A"=="-p" set "ARK_PRINT=1"
  if /I "%%~A"=="--print" set "ARK_PRINT=1"
)
if defined ARK_PRINT goto launch

color 0A
echo(
echo    a8888b.   ARK // local offline hacker assistant
echo   d888888b.  ------------------------------------------
echo   8P"YP"Y88   backend : ollama    model : %OLLAMA_MODEL%
echo   8^|o^|^|o^|88   ctx     : %OLLAMA_NUM_CTX%    net : OFFLINE
echo   8'    .88   ------------------------------------------
echo   8`._.' Y8.  cmds : /recon /scan /enum /osint /decode
echo  d/      `8b.        /hashcrack /payload /ctf /harden
echo  8"       Y8    type your objective, operator.
echo(

:launch
if exist "%ARK_REPO%\dist\cli.js" goto bundle
bun --preload "%ARK_REPO%\plugins\arkMacro.ts" --preload "%ARK_REPO%\plugins\bunBundleDev.ts" "%ARK_REPO%\src\entrypoints\cli.tsx" --strict-mcp-config --append-system-prompt-file "%ARK_PERSONA%" %*
goto done

:bundle
bun --preload "%ARK_REPO%\plugins\bunBundleDev.ts" "%ARK_REPO%\dist\cli.js" --strict-mcp-config --append-system-prompt-file "%ARK_PERSONA%" %*

:done
endlocal
