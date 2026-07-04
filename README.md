# Claude Code 

> **Local-model fork.** This build adds a native **Ollama** backend so the CLI
> runs entirely against local models — no Anthropic account or API key required.
> See [Local models via Ollama](#local-models-via-ollama) below.

## Local models via Ollama

Run the full agent loop (tools, streaming, everything) against a model served by
a local [Ollama](https://ollama.com) instance.

### One command: `arkcli`

A global `arkcli` command is installed (at `%APPDATA%\npm\arkcli.cmd`, already on
PATH). Run it from **any** directory — that directory becomes the agent's
workspace:

```powershell
ollama serve                    # start Ollama, pull a model once:
ollama pull llama3.1            #   (or a coder model, e.g. qwen2.5-coder:7b)

arkcli                          # interactive session in the current folder
arkcli -p "summarize this project"   # one-shot
arkcli --help                   # any Claude Code flag works
```

`arkcli` presets `CLAUDE_CODE_USE_OLLAMA` and the other env vars below, so there
is nothing else to configure. Override the model for one run:

```powershell
$env:OLLAMA_MODEL="qwen2.5-coder:7b"; arkcli -p "refactor foo.ts"
```

**Switching models mid-session.** Just like stock Claude Code, run `/model` in an
interactive session to pick from a roster of 25 well-known Ollama models (coding,
reasoning, and general, small→large). Selecting one switches the live model
immediately — no restart. Any models you've already `ollama pull`ed are listed
first and tagged **· installed**; the rest are still selectable and will prompt
you to pull them on first use. Your configured `OLLAMA_MODEL` always appears too.

> To relocate the repo, edit `ARK_REPO` at the top of `%APPDATA%\npm\arkcli.cmd`.

> **Terminal note.** Run `arkcli` in **PowerShell**, **Command Prompt**, or
> **Windows Terminal**. **Git Bash / MINGW (mintty) cannot send keystrokes to
> the interactive UI** — Enter and arrow keys will appear dead. If you must use
> Git Bash, prefix with winpty: `winpty arkcli`. The local build skips all
> onboarding / trust / API-key dialogs, so a proper terminal drops you straight
> at the prompt.

### Manual invocation

Or set the environment variables yourself and run the CLI directly:

| Variable | Default | Purpose |
|----------|---------|---------|
| `CLAUDE_CODE_USE_OLLAMA` | — | Set to `1` to route all model calls to Ollama. |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Ollama server URL. |
| `OLLAMA_MODEL` | `llama3.1:latest` | Model tag to use for every request. |
| `OLLAMA_NUM_CTX` | `32768` | Context window (tokens) requested from Ollama. |

```bash
CLAUDE_CODE_USE_OLLAMA=1 OLLAMA_MODEL=qwen2.5-coder:7b bun run start -- -p "hello"
```

**How it works.** Nothing downstream changed. A single translation shim
(`src/services/api/ollama.ts`) is handed to the Anthropic SDK as a custom
`fetch`; it converts each outgoing Anthropic Messages request into an Ollama
`/api/chat` call and re-encodes the reply (streaming SSE, tool calls, and
`count_tokens`) back into the exact Anthropic wire format the app expects. The
provider is selected in `src/utils/model/providers.ts` and wired in
`src/services/api/client.ts`, mirroring the existing Bedrock/Vertex/Foundry
branches.

**Model choice matters.** Claude Code's system prompt is large and assumes a
frontier model. Small models (e.g. 8B) work but can be inconsistent at agentic
tool use — prefer a larger or coder-tuned model (`qwen2.5-coder`, `llama3.3`,
etc.) for reliable tool calling.

## Quick Setup

### Prerequisites

- **[Bun](https://bun.sh)** v1.3+ (the project's runtime)
- **Node.js** v18+ (for npm package installation)
- An **Anthropic API key** (set as `ANTHROPIC_API_KEY` environment variable)

### Install & Run

```bash
# 1. Install Bun (if not already installed)
curl -fsSL https://bun.sh/install | bash
source ~/.bash_profile  # or restart your terminal

# 2. Install dependencies
npm install --legacy-peer-deps

# 3. Run Claude Code
bun run start

# Or with arguments:
bun run start -- --help
bun run start -- --version
bun run start -- -p "Hello Claude"
```

### Available Scripts

| Script | Description |
|--------|-------------|
| `bun run start` | Run Claude Code CLI |
| `bun run dev` | Run with hot-reloading (--watch) |
| `bun run build` | Bundle for production |
| `bun run typecheck` | Run TypeScript type checking |

### Environment Variables

| Variable | Description |
|----------|-------------|
| `ANTHROPIC_API_KEY` | Your Anthropic API key (required to use Claude) |
| `FEATURE_FLAGS` | Comma-separated list of feature flags to enable (e.g., `KAIROS,VOICE_MODE`) |

### Notes

- Some modules from the original source were not included in the leak (Anthropic-internal `@ant/*` packages, some tools). These have been replaced with stubs that export no-ops.
- The `bun:bundle` feature flag system is shimmed via a Bun plugin at `plugins/bunBundleDev.ts`. All flags default to `false` unless enabled via `FEATURE_FLAGS`.
- The `MACRO.*` build-time constants are defined in `bunfig.toml` and injected by Bun's `--define` system.

---

## Overview

Claude Code is Anthropic's official CLI tool that lets you interact with Claude directly from the terminal to perform software engineering tasks — editing files, running commands, searching codebases, managing git workflows, and more.

This repository contains the leaked `src/` directory.

- **Leaked on**: 2026-03-31
- **Language**: TypeScript
- **Runtime**: Bun
- **Terminal UI**: React + [Ink](https://github.com/vadimdemedes/ink) (React for CLI)
- **Scale**: ~1,900 files, 512,000+ lines of code

---

## Directory Structure

```
src/
├── main.tsx                 # Entrypoint (Commander.js-based CLI parser)
├── commands.ts              # Command registry
├── tools.ts                 # Tool registry
├── Tool.ts                  # Tool type definitions
├── QueryEngine.ts           # LLM query engine (core Anthropic API caller)
├── context.ts               # System/user context collection
├── cost-tracker.ts          # Token cost tracking
│
├── commands/                # Slash command implementations (~50)
├── tools/                   # Agent tool implementations (~40)
├── components/              # Ink UI components (~140)
├── hooks/                   # React hooks
├── services/                # External service integrations
├── screens/                 # Full-screen UIs (Doctor, REPL, Resume)
├── types/                   # TypeScript type definitions
├── utils/                   # Utility functions
│
├── bridge/                  # IDE integration bridge (VS Code, JetBrains)
├── coordinator/             # Multi-agent coordinator
├── plugins/                 # Plugin system
├── skills/                  # Skill system
├── keybindings/             # Keybinding configuration
├── vim/                     # Vim mode
├── voice/                   # Voice input
├── remote/                  # Remote sessions
├── server/                  # Server mode
├── memdir/                  # Memory directory (persistent memory)
├── tasks/                   # Task management
├── state/                   # State management
├── migrations/              # Config migrations
├── schemas/                 # Config schemas (Zod)
├── entrypoints/             # Initialization logic
├── ink/                     # Ink renderer wrapper
├── buddy/                   # Companion sprite (Easter egg)
├── native-ts/               # Native TypeScript utils
├── outputStyles/            # Output styling
├── query/                   # Query pipeline
└── upstreamproxy/           # Proxy configuration
```

---

## Core Architecture

### 1. Tool System (`src/tools/`)

Every tool Claude Code can invoke is implemented as a self-contained module. Each tool defines its input schema, permission model, and execution logic.

| Tool | Description |
|---|---|
| `BashTool` | Shell command execution |
| `FileReadTool` | File reading (images, PDFs, notebooks) |
| `FileWriteTool` | File creation / overwrite |
| `FileEditTool` | Partial file modification (string replacement) |
| `GlobTool` | File pattern matching search |
| `GrepTool` | ripgrep-based content search |
| `WebFetchTool` | Fetch URL content |
| `WebSearchTool` | Web search |
| `AgentTool` | Sub-agent spawning |
| `SkillTool` | Skill execution |
| `MCPTool` | MCP server tool invocation |
| `LSPTool` | Language Server Protocol integration |
| `NotebookEditTool` | Jupyter notebook editing |
| `TaskCreateTool` / `TaskUpdateTool` | Task creation and management |
| `SendMessageTool` | Inter-agent messaging |
| `TeamCreateTool` / `TeamDeleteTool` | Team agent management |
| `EnterPlanModeTool` / `ExitPlanModeTool` | Plan mode toggle |
| `EnterWorktreeTool` / `ExitWorktreeTool` | Git worktree isolation |
| `ToolSearchTool` | Deferred tool discovery |
| `CronCreateTool` | Scheduled trigger creation |
| `RemoteTriggerTool` | Remote trigger |
| `SleepTool` | Proactive mode wait |
| `SyntheticOutputTool` | Structured output generation |

### 2. Command System (`src/commands/`)

User-facing slash commands invoked with `/` prefix.

| Command | Description |
|---|---|
| `/commit` | Create a git commit |
| `/review` | Code review |
| `/compact` | Context compression |
| `/mcp` | MCP server management |
| `/config` | Settings management |
| `/doctor` | Environment diagnostics |
| `/login` / `/logout` | Authentication |
| `/memory` | Persistent memory management |
| `/skills` | Skill management |
| `/tasks` | Task management |
| `/vim` | Vim mode toggle |
| `/diff` | View changes |
| `/cost` | Check usage cost |
| `/theme` | Change theme |
| `/context` | Context visualization |
| `/pr_comments` | View PR comments |
| `/resume` | Restore previous session |
| `/share` | Share session |
| `/desktop` | Desktop app handoff |
| `/mobile` | Mobile app handoff |

### 3. Service Layer (`src/services/`)

| Service | Description |
|---|---|
| `api/` | Anthropic API client, file API, bootstrap |
| `mcp/` | Model Context Protocol server connection and management |
| `oauth/` | OAuth 2.0 authentication flow |
| `lsp/` | Language Server Protocol manager |
| `analytics/` | GrowthBook-based feature flags and analytics |
| `plugins/` | Plugin loader |
| `compact/` | Conversation context compression |
| `policyLimits/` | Organization policy limits |
| `remoteManagedSettings/` | Remote managed settings |
| `extractMemories/` | Automatic memory extraction |
| `tokenEstimation.ts` | Token count estimation |
| `teamMemorySync/` | Team memory synchronization |

### 4. Bridge System (`src/bridge/`)

A bidirectional communication layer connecting IDE extensions (VS Code, JetBrains) with the Claude Code CLI.

- `bridgeMain.ts` — Bridge main loop
- `bridgeMessaging.ts` — Message protocol
- `bridgePermissionCallbacks.ts` — Permission callbacks
- `replBridge.ts` — REPL session bridge
- `jwtUtils.ts` — JWT-based authentication
- `sessionRunner.ts` — Session execution management

### 5. Permission System (`src/hooks/toolPermission/`)

Checks permissions on every tool invocation. Either prompts the user for approval/denial or automatically resolves based on the configured permission mode (`default`, `plan`, `bypassPermissions`, `auto`, etc.).

### 6. Feature Flags

Dead code elimination via Bun's `bun:bundle` feature flags:

```typescript
import { feature } from 'bun:bundle'

// Inactive code is completely stripped at build time
const voiceCommand = feature('VOICE_MODE')
  ? require('./commands/voice/index.js').default
  : null
```

Notable flags: `PROACTIVE`, `KAIROS`, `BRIDGE_MODE`, `DAEMON`, `VOICE_MODE`, `AGENT_TRIGGERS`, `MONITOR_TOOL`

---

## Key Files in Detail

### `QueryEngine.ts` (~46K lines)

The core engine for LLM API calls. Handles streaming responses, tool-call loops, thinking mode, retry logic, and token counting.

### `Tool.ts` (~29K lines)

Defines base types and interfaces for all tools — input schemas, permission models, and progress state types.

### `commands.ts` (~25K lines)

Manages registration and execution of all slash commands. Uses conditional imports to load different command sets per environment.

### `main.tsx`

Commander.js-based CLI parser + React/Ink renderer initialization. At startup, parallelizes MDM settings, keychain prefetch, and GrowthBook initialization for faster boot.

---

## Tech Stack

| Category | Technology |
|---|---|
| Runtime | [Bun](https://bun.sh) |
| Language | TypeScript (strict) |
| Terminal UI | [React](https://react.dev) + [Ink](https://github.com/vadimdemedes/ink) |
| CLI Parsing | [Commander.js](https://github.com/tj/commander.js) (extra-typings) |
| Schema Validation | [Zod v4](https://zod.dev) |
| Code Search | [ripgrep](https://github.com/BurntSushi/ripgrep) (via GrepTool) |
| Protocols | [MCP SDK](https://modelcontextprotocol.io), LSP |
| API | [Anthropic SDK](https://docs.anthropic.com) |
| Telemetry | OpenTelemetry + gRPC |
| Feature Flags | GrowthBook |
| Auth | OAuth 2.0, JWT, macOS Keychain |

---

## Notable Design Patterns

### Parallel Prefetch

Startup time is optimized by prefetching MDM settings, keychain reads, and API preconnect in parallel — before heavy module evaluation begins.

```typescript
// main.tsx — fired as side-effects before other imports
startMdmRawRead()
startKeychainPrefetch()
```

### Lazy Loading

Heavy modules (OpenTelemetry ~400KB, gRPC ~700KB) are deferred via dynamic `import()` until actually needed.

### Agent Swarms

Sub-agents are spawned via `AgentTool`, with `coordinator/` handling multi-agent orchestration. `TeamCreateTool` enables team-level parallel work.

### Skill System

Reusable workflows defined in `skills/` and executed through `SkillTool`. Users can add custom skills.

### Plugin Architecture

Built-in and third-party plugins are loaded through the `plugins/` subsystem.

---

## Disclaimer

This repository archives source code that was leaked from Anthropic's npm registry on **2026-03-31**. All original source code is the property of [Anthropic](https://www.anthropic.com).
