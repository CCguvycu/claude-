# ARKX FORGE

A local AI development command center. Forge is a control layer for autonomous
coding agents working on repositories on your own machine: you select a repo,
give it a development task, and watch a pipeline of Claude-driven agents analyse
the code, plan, implement, test, debug and review — with every command, every
file change and every diff visible, and destructive actions gated behind your
explicit approval.

It is not a chat UI. There is no conversation transcript: there is a task, a
workflow, an activity stream, a set of changed files, a diff and a review.

---

## Starting Forge

```bash
cd arkx-forge
python3 -m pip install -e .          # or: pip install -r requirements.txt
export ANTHROPIC_API_KEY=sk-ant-...  # server-side only, never sent to the browser
forge                                # starts the server on http://127.0.0.1:8420
```

Open <http://127.0.0.1:8420>, click **+ ADD**, paste the absolute path of a
repository, type a task, press **EXECUTE TASK**.

Without installing the package:

```bash
pip install -r requirements.txt
PYTHONPATH=. python3 -m forge serve
```

## Environment variables

| Variable | Required | Default | Purpose |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | **yes** | – | Claude credential. Read by the server process only; never serialised into an API response, never given to the browser, and stripped from the environment of every shell command an agent runs. `ANTHROPIC_AUTH_TOKEN` is accepted as an alternative. |
| `FORGE_HOST` | no | `127.0.0.1` | Bind address. Loopback by default; Forge has no authentication, so do not expose it. |
| `FORGE_PORT` | no | `8420` | Server port. |
| `FORGE_MODEL` | no | `claude-opus-5-5` | Claude model id. |
| `FORGE_EFFORT` | no | `high` | `low` \| `medium` \| `high` \| `xhigh` \| `max`. |
| `FORGE_MAX_TOKENS` | no | `32000` | Output ceiling per agent turn. |
| `FORGE_PROVIDER` | no | `anthropic` | `anthropic`, `mock` (offline double), or `module.path:ClassName` for a custom provider. |
| `FORGE_HOME` | no | `~/.arkx-forge` | Database and CLI state. |
| `FORGE_COMMAND_TIMEOUT` | no | `300` | Per-command wall-clock limit, seconds. |
| `FORGE_CONFIRM_TIMEOUT` | no | `600` | How long a task waits for your approval before denying, seconds. |
| `FORGE_MAX_ITERATIONS` | no | `40` | Tool round-trip ceiling per stage. |

## Architecture

```
forge/
  config.py              settings from the environment; credentials never leave the server
  events.py              EventBus — per-application pub/sub with sequence numbers
  db.py                  SQLite: projects, tasks, events, file_changes, commands, stages
  llm/
    base.py              Provider protocol, Turn/ToolCall — vendor-neutral
    anthropic_client.py  Claude via the official SDK: streaming, adaptive thinking, effort
    factory.py           build_provider(): anthropic | mock | module.path:ClassName
    mock.py              deterministic offline double for development and tests
  tools/
    safety.py            command classifier (allow/confirm/block) and path confinement
    fs_tools.py          read / list / grep / write / edit / delete, confined to the project
    shell_tools.py       controlled execution: stdout, stderr, exit code, duration, timeout
    git_tools.py         branch, status, diff, log, commit — never pushes
    registry.py          tool schemas for the model + validated dispatch + approval gating
  agents/
    roles.py             Builder, Debugger, Reviewer, Architect, Researcher, Tester (+Summary)
    orchestrator.py      the stage pipeline and the tool loop
    manager.py           task lifecycle: launch, stream, confirm, cancel
  server/
    app.py               FastAPI: JSON API, SSE stream, static frontend
  static/                dark UI — index.html, css/forge.css, js/{api,stream,render,app}.js
  cli.py                 the `forge` command, talks to the backend over HTTP
```

**Data flow.** The browser and the CLI are both HTTP clients of the same
backend. A task runs as an asyncio task in the server; agents emit events into a
per-application `EventBus`; one sink persists them to SQLite and every SSE
subscriber receives them live. Each event carries a monotonic sequence number so
a client that replays stored history and then connects to the live stream
renders each event exactly once.

**Agent workflow.** Each stage is one role running its own tool loop. Stage
outputs are passed forward as a context document rather than one ever-growing
conversation, which keeps each role focused and the token cost predictable.

```
task → ANALYSIS → PLAN → IMPLEMENT → TEST → [DEBUG → TEST]×2 → REVIEW → SUMMARY
       Researcher Architect Builder  Tester   Debugger        Reviewer
       read-only  read-only  full    shell    full            read-only
```

The DEBUG loop runs only when the tester reports a failure, at most twice.

**Adding a provider.** Implement `turn(system, messages, tools, hooks) -> Turn`
(see `llm/base.py`) and point `FORGE_PROVIDER` at `your.module:YourClass`. The
agent layer is untouched. The UI labels any non-Claude provider as such rather
than displaying a Claude model name.

## Safety

- The API key is read from the environment by the server, is absent from every
  API response, and is removed from the environment of every command an agent runs.
- File operations resolve inside the project root; traversal, absolute paths and
  symlink escapes are refused, as are writes inside `.git/`.
- Every command is classified before it runs:
  - **blocked outright**, with no approval offered: recursive deletion of a root,
    home or any path outside the project; disk formatting and raw device writes;
    fork bombs; `sudo`; piping a download into a shell; reading private keys or
    cloud credentials; sending credentials over the network; `git push`; package
    publishing; history rewriting; power-state changes.
  - **approval required**: deleting files, `git reset --hard`, `git clean`,
    rebase/cherry-pick/revert, dependency installs, recursive permission changes,
    `docker`, `kill`, `ssh`, `make install`, absolute-path redirects, and commits.
  - **allowed**: everything else, e.g. tests, builds, linters, reads.
  A compound command is split on shell operators and the strictest verdict wins.
- Deleting a file requires both a stated reason and human approval.
- With no approver attached, a command needing approval does not run.
- Forge never pushes to a remote. Commits happen only when you ask for one.

This raises the cost of an accident; it is not a sandbox. Run Forge on
repositories you trust, and read what it proposes.

## CLI

```bash
forge                                   # start the server
forge project                           # list projects (* = active)
forge project add /path/to/repo         # register and select a repository
forge project use 2                     # switch active project
forge task "add auth to this app"       # run a task, streaming into the terminal
forge task "..." --detach               # start it and return immediately
forge status                            # server, model, credentials, branch, tree, commits
forge history -n 20                     # recent tasks
forge review [task_id]                  # reviewer verdict + final summary
forge test [task_id] -v                 # test outcome and the commands that produced it
forge diff                              # uncommitted diff
forge commit -m "message"               # create a commit (never pushes)
forge cancel [task_id]                  # stop a running task
forge agents                            # the six roles and their tool surfaces
```

`forge task` prompts for approval in the terminal when a command is gated, the
same gate the web UI shows.

## HTTP API

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/health` | liveness |
| GET | `/api/config` | model, provider, effort, agent catalog (no credentials) |
| GET/POST | `/api/projects` | list / register |
| GET/DELETE | `/api/projects/{id}` | detail / remove from Forge |
| GET | `/api/projects/{id}/git` | branch, status, commits |
| GET | `/api/projects/{id}/diff` | unified diff |
| POST | `/api/projects/{id}/commit` | stage and commit |
| GET/POST | `/api/tasks` | history / start a task |
| GET | `/api/tasks/{id}` | task with stages, files, commands, events |
| POST | `/api/tasks/{id}/cancel` | cancel |
| POST | `/api/tasks/{id}/confirm` | approve or deny a gated command |
| GET | `/api/pending` | outstanding approval requests |
| GET | `/api/stream[?task_id=]` | SSE activity stream |

## Tests

```bash
python3 -m pip install -e ".[dev]"
python3 -m pytest -q          # 150 tests, no network and no credentials required
```

Offline throughout: the mock provider covers the pipeline, and a local HTTP
server impersonating `POST /v1/messages` verifies the real Claude request shape
and streaming parse.
