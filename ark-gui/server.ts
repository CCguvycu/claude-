// ARK GUI server — a local hacker web UI that drives the ARK CLI.
//
// It serves ark-gui/index.html and bridges a browser WebSocket to the ARK
// (Claude Code) CLI running in stream-json mode against the local Ollama model.
// Each user turn spawns the CLI with --continue so the conversation persists in
// the working directory; the CLI's JSON events are translated to a small WS
// contract the page understands. Everything ARK can do (tools, persona, /commands)
// works, because this drives the real agent — it isn't a reimplementation.

import { join, dirname } from 'path'
import { fileURLToPath } from 'url'
import { tmpdir } from 'os'
import { writeFileSync } from 'fs'

const HERE = dirname(fileURLToPath(import.meta.url))
const REPO = join(HERE, '..')
const PORT = Number(process.env.ARK_GUI_PORT || 8788)
const MODEL = process.env.OLLAMA_MODEL || 'qwen2.5-coder:7b'
// Directory the agent operates in (its "workspace"). Defaults to where you
// launched arkgui; override with ARK_GUI_CWD.
const WORKDIR = process.env.ARK_GUI_CWD || process.cwd()

const HTML = await Bun.file(join(HERE, 'index.html')).text()

const ENV = {
  ...process.env,
  CLAUDE_CODE_USE_OLLAMA: '1',
  // Force native (Node) file search for command/skill discovery. The bundled
  // ripgrep can be absent; this guarantees ~/.claude/commands/*.md still load.
  CLAUDE_CODE_USE_NATIVE_FILE_SEARCH: '1',
  OLLAMA_MODEL: MODEL,
  ANTHROPIC_API_KEY: process.env.ANTHROPIC_API_KEY || 'ollama',
  CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC: '1',
  DISABLE_TELEMETRY: '1',
  DISABLE_AUTOUPDATER: '1',
  DISABLE_ERROR_REPORTING: '1',
}

type Client = { busy: boolean; proc?: any; stdin?: any; ready?: boolean }

function send(ws: any, obj: unknown) {
  try {
    ws.send(JSON.stringify(obj))
  } catch {}
}

// Translate one CLI stream-json line into zero+ WS messages.
function translate(ws: any, line: string) {
  let ev: any
  try {
    ev = JSON.parse(line)
  } catch {
    return
  }
  if (ev.type === 'system' && ev.subtype === 'init') {
    // model shown to the user is the real Ollama tag
    send(ws, { type: 'ready', model: MODEL })
    return
  }
  if (ev.type === 'assistant' && ev.message?.content) {
    for (const b of ev.message.content) {
      if (b.type === 'text' && b.text) send(ws, { type: 'assistant', text: b.text })
      else if (b.type === 'tool_use')
        send(ws, { type: 'tool_use', name: b.name, input: b.input })
    }
    return
  }
  if (ev.type === 'user' && ev.message?.content) {
    for (const b of ev.message.content) {
      if (b.type === 'tool_result') {
        const c = b.content
        const text =
          typeof c === 'string'
            ? c
            : Array.isArray(c)
              ? c.map((x: any) => x?.text ?? '').join('\n')
              : JSON.stringify(c)
        send(ws, { type: 'tool_result', content: text, ok: !b.is_error })
      }
    }
    return
  }
  if (ev.type === 'result') {
    send(ws, { type: 'result', subtype: ev.subtype })
  }
}

// Spawn ONE persistent ARK process per connection: stdin stays open so each
// turn is a JSON line and the whole conversation stays in one process (startup
// paid once, context persists in-memory). Uses --input-format stream-json.
function ensureProc(ws: any, client: Client) {
  if (client.proc) return
  const args = [
    '--preload',
    join(REPO, 'plugins', 'bunBundleDev.ts'),
    join(REPO, 'dist', 'cli.js'),
    '-p',
    '--input-format',
    'stream-json',
    '--output-format',
    'stream-json',
    '--verbose',
    '--dangerously-skip-permissions',
    // Offline assistant: ignore claude.ai-connected MCP servers (Gmail/Drive/…).
    // They add ~25 irrelevant tools that hurt a small model's tool selection.
    '--strict-mcp-config',
    '--append-system-prompt-file',
    join(REPO, 'ark-persona.md'),
  ]
  // Use the absolute path of THIS bun binary — spawning bare "bun" fails with
  // ENOENT/uv_spawn on Windows when PATH resolution doesn't cover bun.exe/.cmd
  // (e.g. launched from a plain console via arkgui.cmd).
  const BUN = process.execPath || 'bun'
  const proc = Bun.spawn([BUN, ...args], {
    cwd: WORKDIR,
    env: ENV,
    stdin: 'pipe',
    stdout: 'pipe',
    stderr: 'pipe',
  })
  client.proc = proc
  client.stdin = proc.stdin

  // Reader loop for the process lifetime — forwards every event to this ws and
  // clears busy when a turn's `result` arrives.
  ;(async () => {
    const reader = proc.stdout.getReader()
    const dec = new TextDecoder()
    let buf = ''
    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      buf += dec.decode(value, { stream: true })
      let nl: number
      while ((nl = buf.indexOf('\n')) !== -1) {
        const line = buf.slice(0, nl).trim()
        buf = buf.slice(nl + 1)
        if (!line) continue
        translate(ws, line)
        if (line.includes('"type":"result"')) client.busy = false
      }
    }
    // process ended
    const err = await new Response(proc.stderr).text().catch(() => '')
    if (client.busy) {
      send(ws, {
        type: 'error',
        text: `agent ended. ${err.split('\n').filter(Boolean).slice(-1)[0] || ''}`,
      })
      send(ws, { type: 'result' })
      client.busy = false
    }
    client.proc = undefined
  })()
}

function sendTurn(client: Client, prompt: string) {
  const line =
    JSON.stringify({
      type: 'user',
      message: { role: 'user', content: prompt },
      parent_tool_use_id: null,
    }) + '\n'
  client.stdin.write(new TextEncoder().encode(line))
  client.stdin.flush?.()
}

const handlers = {
  fetch(req: Request, srv: any) {
    if (srv.upgrade(req, { data: { started: false, busy: false } })) return
    const url = new URL(req.url)
    if (url.pathname === '/' || url.pathname === '/index.html') {
      return new Response(HTML, { headers: { 'content-type': 'text/html; charset=utf-8' } })
    }
    return new Response('not found', { status: 404 })
  },
  websocket: {
    open(ws) {
      send(ws, { type: 'ready', model: MODEL })
      send(ws, { type: 'status', text: `ARK online // model=${MODEL} // cwd=${WORKDIR}` })
    },
    async message(ws, raw) {
      let m: any
      try {
        m = JSON.parse(String(raw))
      } catch {
        return
      }
      if (m.type !== 'prompt' || !m.text?.trim()) return
      const client = ws.data
      if (client.busy) return
      try {
        ensureProc(ws, client)
        client.busy = true
        sendTurn(client, m.text.trim())
      } catch (e: any) {
        send(ws, { type: 'error', text: String(e?.message || e) })
        client.busy = false
      }
    },
    close(ws: any) {
      try {
        ws.data.stdin?.end?.()
        ws.data.proc?.kill()
      } catch {}
    },
  },
}

// Bind to the first free port from PORT upward, so a stale/zombie process on
// the default port never blocks startup. Write the chosen URL where the
// launcher can read it to open the right browser tab.
let server: any
let chosen = PORT
for (let p = PORT; p < PORT + 12; p++) {
  try {
    server = Bun.serve<Client>({ port: p, ...(handlers as any) })
    chosen = p
    break
  } catch (e) {
    if (p === PORT + 11) {
      console.error(`Could not bind any port in ${PORT}-${PORT + 11}: ${e}`)
      process.exit(1)
    }
  }
}
const URL_STR = `http://localhost:${chosen}`
try {
  writeFileSync(join(tmpdir(), 'ark-gui-url.txt'), URL_STR)
} catch {}

console.error(`\n  ARK GUI online  ->  ${URL_STR}`)
console.error(`  model: ${MODEL}   workspace: ${WORKDIR}\n`)
