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

4. **Model** — needs Ollama running with a tool-capable model:
   ```
   ollama pull qwen2.5-coder:7b
   ```

Then `arkcli` works from any directory. Persona lives in `../ark-persona.md`.

## What ARK is
Claude Code re-pointed at a local Ollama model via a fetch-layer translation
shim (`src/services/api/ollama.ts`), with a hacker persona, custom security
commands, and a single global `arkcli` command. Fully offline, no Anthropic
account.
