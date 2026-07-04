/**
 * Ollama backend shim.
 *
 * Claude Code is built entirely around the Anthropic Messages (beta) wire
 * format: the SDK is asked for `anthropic.beta.messages.create(...)` and every
 * downstream consumer expects Anthropic-shaped content blocks and SSE stream
 * events (`message_start`, `content_block_delta`, `tool_use`, …).
 *
 * Ollama speaks a different protocol (`POST /api/chat`, NDJSON streaming).
 * Rather than touch the ~1900 downstream files, we intercept at the `fetch`
 * layer: the Anthropic SDK is handed a custom `fetch` that translates the
 * outgoing Anthropic request into an Ollama request, calls Ollama, and
 * re-encodes the response back into the exact Anthropic wire format the rest
 * of the app already understands.
 *
 * Nothing here is Anthropic-specific plumbing — it's a pure protocol adapter,
 * so the agent loop, tool dispatch, streaming UI, etc. all work unchanged
 * against a local model.
 */

import { randomUUID } from 'crypto'

// ---------------------------------------------------------------------------
// Config
// ---------------------------------------------------------------------------

export function getOllamaBaseUrl(): string {
  return (process.env.OLLAMA_BASE_URL || 'http://localhost:11434').replace(
    /\/+$/,
    '',
  )
}

function getDefaultOllamaModel(): string {
  return process.env.OLLAMA_MODEL || 'llama3.1:latest'
}

function getNumCtx(): number {
  const n = parseInt(process.env.OLLAMA_NUM_CTX || '', 10)
  return Number.isFinite(n) && n > 0 ? n : 32768
}

/**
 * Map an incoming (Anthropic-style) model id onto a real Ollama model tag.
 * Ollama tags contain a ':' (e.g. "llama3.1:latest", "qwen2.5-coder:7b").
 * Anything else — the default "claude-…" ids the app resolves to, or a bare
 * name — falls back to the configured OLLAMA_MODEL. This means the CLI works
 * whether or not the user passes `--model`.
 */
function mapModel(model: string | undefined): string {
  if (model && model.includes(':')) return model
  if (model && /^(llama|qwen|mistral|gemma|phi|deepseek|codellama|granite)/i.test(model)) {
    return model
  }
  return getDefaultOllamaModel()
}

// ---------------------------------------------------------------------------
// Minimal shapes (we only touch the fields we translate)
// ---------------------------------------------------------------------------

type AnthropicBlock =
  | { type: 'text'; text: string }
  | { type: 'tool_use'; id: string; name: string; input: unknown }
  | {
      type: 'tool_result'
      tool_use_id: string
      content: string | Array<{ type: string; text?: string }>
      is_error?: boolean
    }
  | { type: string; [k: string]: unknown }

interface AnthropicMessage {
  role: 'user' | 'assistant'
  content: string | AnthropicBlock[]
}

interface AnthropicRequest {
  model?: string
  system?: string | Array<{ type: string; text?: string }>
  messages: AnthropicMessage[]
  tools?: Array<{ name: string; description?: string; input_schema: unknown }>
  max_tokens?: number
  temperature?: number
  top_p?: number
  stop_sequences?: string[]
  stream?: boolean
}

interface OllamaToolCall {
  function: { name: string; arguments: Record<string, unknown> }
}

interface OllamaMessage {
  role: 'system' | 'user' | 'assistant' | 'tool'
  content: string
  tool_calls?: OllamaToolCall[]
  tool_name?: string
}

// ---------------------------------------------------------------------------
// Request translation: Anthropic -> Ollama
// ---------------------------------------------------------------------------

function systemToText(
  system: AnthropicRequest['system'],
): string | undefined {
  if (!system) return undefined
  if (typeof system === 'string') return system
  return system
    .map(b => (b.type === 'text' ? b.text ?? '' : ''))
    .join('\n')
    .trim()
}

function blockContentToText(
  content: string | Array<{ type: string; text?: string }>,
): string {
  if (typeof content === 'string') return content
  return content
    .map(b => (b.type === 'text' ? b.text ?? '' : ''))
    .join('\n')
}

function translateMessages(req: AnthropicRequest): OllamaMessage[] {
  const out: OllamaMessage[] = []

  const systemText = systemToText(req.system)
  if (systemText) out.push({ role: 'system', content: systemText })

  for (const msg of req.messages) {
    if (typeof msg.content === 'string') {
      out.push({ role: msg.role, content: msg.content })
      continue
    }

    let text = ''
    const toolCalls: OllamaToolCall[] = []
    const toolResults: OllamaMessage[] = []

    for (const block of msg.content) {
      switch (block.type) {
        case 'text':
          text += (block as { text: string }).text ?? ''
          break
        case 'tool_use': {
          const b = block as { name: string; input: unknown }
          toolCalls.push({
            function: {
              name: b.name,
              arguments: (b.input as Record<string, unknown>) ?? {},
            },
          })
          break
        }
        case 'tool_result': {
          const b = block as {
            content: string | Array<{ type: string; text?: string }>
          }
          toolResults.push({
            role: 'tool',
            content: blockContentToText(b.content),
          })
          break
        }
        // thinking / image / redacted blocks: ignored for local models
        default:
          break
      }
    }

    // Tool results must land as their own `tool` messages, right after the
    // assistant turn that requested them.
    for (const tr of toolResults) out.push(tr)

    if (text || toolCalls.length > 0) {
      const m: OllamaMessage = { role: msg.role, content: text }
      if (toolCalls.length > 0) m.tool_calls = toolCalls
      out.push(m)
    }
  }

  return out
}

function translateTools(req: AnthropicRequest): unknown[] | undefined {
  if (!req.tools || req.tools.length === 0) return undefined
  return req.tools.map(t => ({
    type: 'function',
    function: {
      name: t.name,
      description: t.description ?? '',
      parameters: t.input_schema ?? { type: 'object', properties: {} },
    },
  }))
}

function buildOllamaRequest(
  req: AnthropicRequest,
  stream: boolean,
): Record<string, unknown> {
  const options: Record<string, unknown> = { num_ctx: getNumCtx() }
  // Local models follow "call this tool" instructions far more reliably at a low
  // temperature. Claude Code's main loop asks for ~1.0, which makes small models
  // chatty and prone to describing a command instead of calling the tool. Clamp
  // to a low ceiling; override with OLLAMA_TEMPERATURE (set to the passthrough
  // value, e.g. "1", to disable the clamp).
  const tempOverride = process.env.OLLAMA_TEMPERATURE
  const tempCeiling = tempOverride !== undefined ? Number(tempOverride) : 0.3
  if (typeof req.temperature === 'number')
    options.temperature = Number.isFinite(tempCeiling)
      ? Math.min(req.temperature, tempCeiling)
      : req.temperature
  else if (Number.isFinite(tempCeiling)) options.temperature = tempCeiling
  if (typeof req.top_p === 'number') options.top_p = req.top_p
  if (typeof req.max_tokens === 'number') options.num_predict = req.max_tokens
  if (req.stop_sequences && req.stop_sequences.length > 0) {
    options.stop = req.stop_sequences
  }

  const body: Record<string, unknown> = {
    model: mapModel(req.model),
    messages: translateMessages(req),
    stream,
    options,
    // Keep the model resident in VRAM between calls so separate `arkcli`
    // invocations don't pay the multi-second reload each time. Override with
    // OLLAMA_KEEP_ALIVE (e.g. "60m", or "-1" to keep loaded until Ollama exits).
    keep_alive: process.env.OLLAMA_KEEP_ALIVE || '30m',
  }
  const tools = translateTools(req)
  if (tools) body.tools = tools
  return body
}

// ---------------------------------------------------------------------------
// Response translation helpers
// ---------------------------------------------------------------------------

function newMessageId(): string {
  return `msg_${randomUUID().replace(/-/g, '')}`
}

function newToolId(): string {
  return `toolu_${randomUUID().replace(/-/g, '')}`
}

function mapStopReason(
  hadToolCalls: boolean,
  doneReason: string | undefined,
): string {
  if (hadToolCalls) return 'tool_use'
  if (doneReason === 'length') return 'max_tokens'
  return 'end_turn'
}

// ---------------------------------------------------------------------------
// Text tool-call recovery
//
// Local models frequently emit a tool call as plain text (often fenced as
// ```json {"name":"Read","arguments":{...}} ```), and Ollama doesn't always
// parse it back into the structured `tool_calls` field. Without recovery the
// agent never executes the tool. We scan assistant text for JSON objects
// shaped like a tool call and lift them out into real tool_use blocks.
// ---------------------------------------------------------------------------

function looksLikeToolCall(
  o: unknown,
): o is { name: string; arguments?: unknown; parameters?: unknown } {
  return (
    !!o &&
    typeof o === 'object' &&
    typeof (o as { name?: unknown }).name === 'string' &&
    ('arguments' in (o as object) || 'parameters' in (o as object))
  )
}

/**
 * Pull tool-call-shaped JSON objects out of assistant text. Returns the text
 * with those objects removed and the recovered tool calls. Balanced-brace scan
 * so it works whether the model fenced the JSON, added prose, or emitted it bare.
 */
function extractTextToolCalls(raw: string): {
  text: string
  toolCalls: OllamaToolCall[]
} {
  const toolCalls: OllamaToolCall[] = []
  const keep: string[] = []
  let cursor = 0
  let i = 0
  while (i < raw.length) {
    if (raw[i] !== '{') {
      i++
      continue
    }
    // Find the matching close brace for the object starting at i.
    let depth = 0
    let inStr = false
    let esc = false
    let end = -1
    for (let j = i; j < raw.length; j++) {
      const c = raw[j]
      if (esc) {
        esc = false
        continue
      }
      if (c === '\\') {
        esc = true
        continue
      }
      if (c === '"') inStr = !inStr
      else if (!inStr && c === '{') depth++
      else if (!inStr && c === '}') {
        depth--
        if (depth === 0) {
          end = j + 1
          break
        }
      }
    }
    if (end === -1) break // unbalanced; leave the rest as text
    const slice = raw.slice(i, end)
    let obj: unknown
    try {
      obj = JSON.parse(slice)
    } catch {
      obj = undefined
    }
    if (looksLikeToolCall(obj)) {
      keep.push(raw.slice(cursor, i))
      const args =
        (obj as { arguments?: unknown }).arguments ??
        (obj as { parameters?: unknown }).parameters ??
        {}
      toolCalls.push({
        function: {
          name: (obj as { name: string }).name,
          arguments:
            args && typeof args === 'object'
              ? (args as Record<string, unknown>)
              : {},
        },
      })
      cursor = end
      i = end
    } else {
      i++
    }
  }
  if (toolCalls.length === 0) return { text: raw, toolCalls: [] }
  keep.push(raw.slice(cursor))
  const text = keep
    .join(' ')
    .replace(/```(?:json|tool_call|tool_code)?/gi, '')
    .replace(/\s+/g, ' ')
    .trim()
  return { text, toolCalls }
}

/**
 * Given Ollama's structured tool_calls (may be empty) and the assistant text,
 * return the final text + tool calls, recovering text-embedded calls only when
 * Ollama didn't already provide structured ones.
 */
function resolveToolCalls(
  structured: OllamaToolCall[] | undefined,
  text: string,
): { text: string; toolCalls: OllamaToolCall[] } {
  if (structured && structured.length > 0) {
    return { text, toolCalls: structured }
  }
  return extractTextToolCalls(text)
}

// ---------------------------------------------------------------------------
// Non-streaming: Ollama JSON -> Anthropic Message JSON
// ---------------------------------------------------------------------------

interface OllamaChatResponse {
  model?: string
  message?: {
    role?: string
    content?: string
    tool_calls?: OllamaToolCall[]
  }
  done_reason?: string
  prompt_eval_count?: number
  eval_count?: number
}

function translateNonStreaming(
  ollama: OllamaChatResponse,
  model: string,
): Record<string, unknown> {
  const content: unknown[] = []
  const { text, toolCalls } = resolveToolCalls(
    ollama.message?.tool_calls,
    ollama.message?.content ?? '',
  )
  if (text) content.push({ type: 'text', text })

  for (const tc of toolCalls) {
    content.push({
      type: 'tool_use',
      id: newToolId(),
      name: tc.function.name,
      input: tc.function.arguments ?? {},
    })
  }

  return {
    id: newMessageId(),
    type: 'message',
    role: 'assistant',
    model,
    content,
    stop_reason: mapStopReason(toolCalls.length > 0, ollama.done_reason),
    stop_sequence: null,
    usage: {
      input_tokens: ollama.prompt_eval_count ?? 0,
      output_tokens: ollama.eval_count ?? 0,
    },
  }
}

// ---------------------------------------------------------------------------
// Streaming: Ollama NDJSON -> Anthropic SSE events
// ---------------------------------------------------------------------------

function sse(event: string, data: unknown): Uint8Array {
  return new TextEncoder().encode(
    `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`,
  )
}

/**
 * Buffered emitter: read the whole Ollama response, recover any text-embedded
 * tool calls, then emit a clean Anthropic SSE sequence. Used when the request
 * carries tools (agentic turns) so a tool call written as prose still executes.
 * Trades token-by-token streaming for correct tool dispatch.
 */
function buildBufferedSseStream(
  ollamaBody: ReadableStream<Uint8Array>,
  model: string,
): ReadableStream<Uint8Array> {
  const reader = ollamaBody.getReader()
  const decoder = new TextDecoder()
  const messageId = newMessageId()

  return new ReadableStream<Uint8Array>({
    async start(controller) {
      let buffer = ''
      let fullText = ''
      const structured: OllamaToolCall[] = []
      let inputTokens = 0
      let outputTokens = 0
      let doneReason: string | undefined

      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        buffer += decoder.decode(value, { stream: true })
        let nl: number
        while ((nl = buffer.indexOf('\n')) !== -1) {
          const line = buffer.slice(0, nl).trim()
          buffer = buffer.slice(nl + 1)
          if (!line) continue
          let chunk: OllamaChatResponse & { done?: boolean }
          try {
            chunk = JSON.parse(line)
          } catch {
            continue
          }
          if (chunk.message?.content) fullText += chunk.message.content
          if (chunk.message?.tool_calls) {
            structured.push(...chunk.message.tool_calls)
          }
          if (typeof chunk.prompt_eval_count === 'number') {
            inputTokens = chunk.prompt_eval_count
          }
          if (typeof chunk.eval_count === 'number') {
            outputTokens = chunk.eval_count
          }
          if (chunk.done_reason) doneReason = chunk.done_reason
        }
      }

      const { text, toolCalls } = resolveToolCalls(structured, fullText)

      controller.enqueue(
        sse('message_start', {
          type: 'message_start',
          message: {
            id: messageId,
            type: 'message',
            role: 'assistant',
            model,
            content: [],
            stop_reason: null,
            stop_sequence: null,
            usage: { input_tokens: inputTokens, output_tokens: 0 },
          },
        }),
      )

      let index = 0
      if (text) {
        controller.enqueue(
          sse('content_block_start', {
            type: 'content_block_start',
            index,
            content_block: { type: 'text', text: '' },
          }),
        )
        controller.enqueue(
          sse('content_block_delta', {
            type: 'content_block_delta',
            index,
            delta: { type: 'text_delta', text },
          }),
        )
        controller.enqueue(
          sse('content_block_stop', {
            type: 'content_block_stop',
            index,
          }),
        )
        index++
      }
      for (const tc of toolCalls) {
        controller.enqueue(
          sse('content_block_start', {
            type: 'content_block_start',
            index,
            content_block: {
              type: 'tool_use',
              id: newToolId(),
              name: tc.function.name,
              input: {},
            },
          }),
        )
        controller.enqueue(
          sse('content_block_delta', {
            type: 'content_block_delta',
            index,
            delta: {
              type: 'input_json_delta',
              partial_json: JSON.stringify(tc.function.arguments ?? {}),
            },
          }),
        )
        controller.enqueue(
          sse('content_block_stop', {
            type: 'content_block_stop',
            index,
          }),
        )
        index++
      }

      controller.enqueue(
        sse('message_delta', {
          type: 'message_delta',
          delta: {
            stop_reason: mapStopReason(toolCalls.length > 0, doneReason),
            stop_sequence: null,
          },
          usage: { input_tokens: inputTokens, output_tokens: outputTokens },
        }),
      )
      controller.enqueue(sse('message_stop', { type: 'message_stop' }))
      controller.close()
    },
    cancel() {
      void reader.cancel()
    },
  })
}

function buildSseStream(
  ollamaBody: ReadableStream<Uint8Array>,
  model: string,
): ReadableStream<Uint8Array> {
  const reader = ollamaBody.getReader()
  const decoder = new TextDecoder()

  const messageId = newMessageId()
  let buffer = ''

  // block bookkeeping
  let nextIndex = 0
  let textOpen = false
  let textIndex = -1
  let sawToolCall = false
  let inputTokens = 0
  let outputTokens = 0
  let doneReason: string | undefined

  return new ReadableStream<Uint8Array>({
    start(controller) {
      // message_start
      controller.enqueue(
        sse('message_start', {
          type: 'message_start',
          message: {
            id: messageId,
            type: 'message',
            role: 'assistant',
            model,
            content: [],
            stop_reason: null,
            stop_sequence: null,
            usage: { input_tokens: 0, output_tokens: 0 },
          },
        }),
      )
    },

    async pull(controller) {
      const emitTextDelta = (text: string) => {
        if (!text) return
        if (!textOpen) {
          textIndex = nextIndex++
          textOpen = true
          controller.enqueue(
            sse('content_block_start', {
              type: 'content_block_start',
              index: textIndex,
              content_block: { type: 'text', text: '' },
            }),
          )
        }
        controller.enqueue(
          sse('content_block_delta', {
            type: 'content_block_delta',
            index: textIndex,
            delta: { type: 'text_delta', text },
          }),
        )
      }

      const closeText = () => {
        if (textOpen) {
          controller.enqueue(
            sse('content_block_stop', {
              type: 'content_block_stop',
              index: textIndex,
            }),
          )
          textOpen = false
        }
      }

      const emitToolCall = (tc: OllamaToolCall) => {
        sawToolCall = true
        closeText()
        const idx = nextIndex++
        controller.enqueue(
          sse('content_block_start', {
            type: 'content_block_start',
            index: idx,
            content_block: {
              type: 'tool_use',
              id: newToolId(),
              name: tc.function.name,
              input: {},
            },
          }),
        )
        controller.enqueue(
          sse('content_block_delta', {
            type: 'content_block_delta',
            index: idx,
            delta: {
              type: 'input_json_delta',
              partial_json: JSON.stringify(tc.function.arguments ?? {}),
            },
          }),
        )
        controller.enqueue(
          sse('content_block_stop', {
            type: 'content_block_stop',
            index: idx,
          }),
        )
      }

      const finish = () => {
        closeText()
        controller.enqueue(
          sse('message_delta', {
            type: 'message_delta',
            delta: {
              stop_reason: mapStopReason(sawToolCall, doneReason),
              stop_sequence: null,
            },
            usage: { output_tokens: outputTokens },
          }),
        )
        controller.enqueue(
          sse('message_stop', { type: 'message_stop' }),
        )
        controller.close()
      }

      while (true) {
        const { done, value } = await reader.read()
        if (done) {
          finish()
          return
        }
        buffer += decoder.decode(value, { stream: true })

        let nl: number
        while ((nl = buffer.indexOf('\n')) !== -1) {
          const line = buffer.slice(0, nl).trim()
          buffer = buffer.slice(nl + 1)
          if (!line) continue

          let chunk: OllamaChatResponse
          try {
            chunk = JSON.parse(line)
          } catch {
            continue
          }

          if (chunk.message?.content) emitTextDelta(chunk.message.content)
          if (chunk.message?.tool_calls) {
            for (const tc of chunk.message.tool_calls) emitToolCall(tc)
          }
          if (typeof chunk.prompt_eval_count === 'number') {
            inputTokens = chunk.prompt_eval_count
          }
          if (typeof chunk.eval_count === 'number') {
            outputTokens = chunk.eval_count
          }
          if (chunk.done_reason) doneReason = chunk.done_reason

          if ((chunk as { done?: boolean }).done) {
            // patch message_start usage retroactively is not possible; the
            // message_delta below carries the final output token count and the
            // app reads input tokens from message_start (0) + our delta. To keep
            // input tokens visible, emit them on the final delta too.
            closeText()
            controller.enqueue(
              sse('message_delta', {
                type: 'message_delta',
                delta: {
                  stop_reason: mapStopReason(sawToolCall, doneReason),
                  stop_sequence: null,
                },
                usage: {
                  input_tokens: inputTokens,
                  output_tokens: outputTokens,
                },
              }),
            )
            controller.enqueue(
              sse('message_stop', { type: 'message_stop' }),
            )
            controller.close()
            return
          }
        }
      }
    },

    cancel() {
      void reader.cancel()
    },
  })
}

// ---------------------------------------------------------------------------
// count_tokens: rough local estimate (Ollama has no count endpoint)
// ---------------------------------------------------------------------------

function estimateTokens(req: AnthropicRequest): number {
  let chars = 0
  const sys = systemToText(req.system)
  if (sys) chars += sys.length
  for (const m of req.messages) {
    if (typeof m.content === 'string') chars += m.content.length
    else
      for (const b of m.content) {
        if (b.type === 'text') chars += ((b as { text?: string }).text ?? '').length
        else chars += JSON.stringify(b).length
      }
  }
  if (req.tools) chars += JSON.stringify(req.tools).length
  return Math.max(1, Math.ceil(chars / 4))
}

// ---------------------------------------------------------------------------
// The fetch shim handed to the Anthropic SDK
// ---------------------------------------------------------------------------

export function createOllamaFetch(baseUrl: string): typeof fetch {
  const ollamaBase = baseUrl.replace(/\/+$/, '')

  return async function ollamaFetch(
    input: RequestInfo | URL,
    init?: RequestInit,
  ): Promise<Response> {
    const url = input instanceof Request ? input.url : String(input)

    // Read the request body the SDK built.
    let rawBody = ''
    if (input instanceof Request) rawBody = await input.text()
    else if (typeof init?.body === 'string') rawBody = init.body

    let req: AnthropicRequest
    try {
      req = JSON.parse(rawBody || '{}')
    } catch {
      req = { messages: [] }
    }

    const jsonHeaders = { 'content-type': 'application/json' }

    // Token counting endpoint — estimate locally.
    if (url.includes('count_tokens')) {
      return new Response(
        JSON.stringify({ input_tokens: estimateTokens(req) }),
        { status: 200, headers: jsonHeaders },
      )
    }

    const wantStream = req.stream === true
    const ollamaReq = buildOllamaRequest(req, wantStream)
    const outModel = mapModel(req.model)

    let ollamaResp: Response
    try {
      ollamaResp = await fetch(`${ollamaBase}/api/chat`, {
        method: 'POST',
        headers: jsonHeaders,
        body: JSON.stringify(ollamaReq),
        signal: init?.signal ?? undefined,
      })
    } catch (e) {
      return new Response(
        JSON.stringify({
          type: 'error',
          error: {
            type: 'api_error',
            message: `Failed to reach Ollama at ${ollamaBase}: ${
              (e as Error).message
            }. Is 'ollama serve' running?`,
          },
        }),
        { status: 502, headers: jsonHeaders },
      )
    }

    if (!ollamaResp.ok) {
      const errText = await ollamaResp.text()
      return new Response(
        JSON.stringify({
          type: 'error',
          error: {
            type: 'api_error',
            message: `Ollama error ${ollamaResp.status}: ${errText}`,
          },
        }),
        { status: ollamaResp.status, headers: jsonHeaders },
      )
    }

    if (wantStream && ollamaResp.body) {
      // When tools are offered (agentic turns), buffer the response so tool
      // calls the model wrote as text are recovered and dispatched. Plain chat
      // turns (no tools) keep token-by-token streaming.
      const hasTools = Array.isArray(req.tools) && req.tools.length > 0
      const stream = hasTools
        ? buildBufferedSseStream(ollamaResp.body, outModel)
        : buildSseStream(ollamaResp.body, outModel)
      return new Response(stream, {
        status: 200,
        headers: {
          'content-type': 'text/event-stream; charset=utf-8',
          'cache-control': 'no-cache',
        },
      })
    }

    // Non-streaming
    const ollamaJson = (await ollamaResp.json()) as OllamaChatResponse
    return new Response(
      JSON.stringify(translateNonStreaming(ollamaJson, outModel)),
      { status: 200, headers: jsonHeaders },
    )
  } as typeof fetch
}
