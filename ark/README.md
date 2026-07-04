# ARK — install / restore

Reproducible copy of the ARK local-hacker setup (the live copies live outside
this repo). To (re)install on this machine:

1. **Launcher** — copy `arkcli.cmd` onto PATH:
   ```
   copy ark\arkcli.cmd %APPDATA%\npm\arkcli.cmd
   ```
   (Edit `ARK_REPO` inside it if the repo path changes. Keep CRLF line endings.)

2. **Commands** — copy the hacker slash-commands to the user commands dir:
   ```
   xcopy /Y ark\commands\*.md %USERPROFILE%\.claude\commands\
   ```

3. **Build the fast bundle** (startup ~1.9s vs ~5.4s from source):
   ```
   bun install
   bun run ark:build
   ```
   `ark:build` also runs `ark:vendor-rg`, which copies a working `rg.exe` into
   `src/utils/vendor/` and `dist/vendor/`. This is REQUIRED: the upstream leak
   ships without a ripgrep binary, and without it custom `/commands` (and the Grep
   tool) silently fail to load. Re-run `bun run ark:vendor-rg` if it ever goes missing.
   The launchers also set `CLAUDE_CODE_USE_NATIVE_FILE_SEARCH=1` as a fallback so
   commands still load even without the binary.

4. **Model** — needs Ollama running with a tool-capable model:
   ```
   ollama pull qwen2.5-coder:7b
   ```

Then `arkcli` works from any directory. Persona lives in `../ark-persona.md`.

5. **GUI** (optional) — hacker web UI. Copy the launcher and run it:
   ```
   copy ark\arkgui.cmd %APPDATA%\npm\arkgui.cmd
   arkgui
   ```
   `arkgui` starts a local Bun server (`ark-gui/server.ts`, default port 8788)
   and opens the browser. The page drives ONE persistent ARK process over a
   WebSocket in stream-json mode, so context persists and startup is paid once.
   ARK operates in the folder you launched `arkgui` from. Close the server
   window to stop it. (`bun run ark:gui` runs the server without opening a browser.)

## What ARK is
Claude Code re-pointed at a local Ollama model via a fetch-layer translation
shim (`src/services/api/ollama.ts`), with a hacker persona, custom security
commands, and a single global `arkcli` command. Fully offline, no Anthropic
account.
