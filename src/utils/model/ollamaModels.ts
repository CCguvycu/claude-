/**
 * ARK: Ollama model roster for the `/model` picker.
 *
 * When the Ollama backend is active (CLAUDE_CODE_USE_OLLAMA=1), the normal
 * Claude/Bedrock/Vertex model lists are meaningless — every request is routed
 * to a local Ollama model instead. This module supplies the picker with a
 * curated roster of well-known Ollama tags so the CLI behaves like stock Claude
 * Code (pick a model from a list) but against local models.
 *
 * How selection takes effect: a ModelOption's `value` becomes the request's
 * `model` field, which the Ollama fetch shim (services/api/ollama.ts `mapModel`)
 * passes straight through to Ollama whenever it contains a ':' tag. So every
 * entry here uses a real, pullable Ollama tag — selecting it switches the live
 * model with no other plumbing.
 *
 * Not every tag is guaranteed to be pulled locally; unpulled models simply
 * error on first use with Ollama's "pull it first" message. Installed models
 * are surfaced first (see getOllamaModelOptions) so the user's real inventory
 * leads the list.
 */

import type { ModelOption } from './modelOptions.js'

export interface OllamaRosterEntry {
  /** Real Ollama tag, e.g. "qwen2.5-coder:7b". Must contain ':'. */
  tag: string
  /** Short label shown in the picker. */
  label: string
  /** One-line description. */
  description: string
}

/**
 * Curated roster of 25 popular Ollama models spanning coding, general, and
 * reasoning use cases at small→large sizes. Keep tags canonical (pullable via
 * `ollama pull <tag>`).
 */
export const OLLAMA_MODEL_ROSTER: OllamaRosterEntry[] = [
  // — Coding —
  { tag: 'qwen2.5-coder:7b', label: 'Qwen2.5 Coder 7B', description: 'Strong local coder · fast · great default for code' },
  { tag: 'qwen2.5-coder:14b', label: 'Qwen2.5 Coder 14B', description: 'Better reasoning on code · needs ~10GB VRAM' },
  { tag: 'qwen2.5-coder:32b', label: 'Qwen2.5 Coder 32B', description: 'Best local coder · needs ~20GB VRAM' },
  { tag: 'deepseek-coder-v2:16b', label: 'DeepSeek Coder V2 16B', description: 'MoE coder · fast for its quality' },
  { tag: 'codellama:13b', label: 'Code Llama 13B', description: "Meta's code model · solid all-rounder" },
  // — Reasoning —
  { tag: 'deepseek-r1:7b', label: 'DeepSeek-R1 7B', description: 'Reasoning model · shows chain-of-thought' },
  { tag: 'deepseek-r1:14b', label: 'DeepSeek-R1 14B', description: 'Stronger reasoning · slower' },
  { tag: 'phi4:14b', label: 'Phi-4 14B', description: "Microsoft Phi-4 · strong reasoning for its size" },
  // — General (Llama) —
  { tag: 'llama3.1:8b', label: 'Llama 3.1 8B', description: 'Meta general-purpose · reliable all-rounder' },
  { tag: 'llama3.1:70b', label: 'Llama 3.1 70B', description: 'Most capable Llama · needs ~40GB VRAM' },
  { tag: 'llama3.2:3b', label: 'Llama 3.2 3B', description: 'Fast small model · low VRAM' },
  { tag: 'llama3.2:1b', label: 'Llama 3.2 1B', description: 'Tiniest · runs on almost anything' },
  { tag: 'llama3.3:70b', label: 'Llama 3.3 70B', description: 'Latest large Llama · near-405B quality' },
  // — General (Qwen) —
  { tag: 'qwen2.5:7b', label: 'Qwen2.5 7B', description: 'Well-rounded general model' },
  { tag: 'qwen2.5:14b', label: 'Qwen2.5 14B', description: 'Larger general Qwen · better instruction following' },
  { tag: 'qwen3:8b', label: 'Qwen3 8B', description: 'Newer Qwen generation · hybrid reasoning' },
  { tag: 'qwen3:14b', label: 'Qwen3 14B', description: 'Larger Qwen3 · strong general + reasoning' },
  // — General (Mistral) —
  { tag: 'mistral:7b', label: 'Mistral 7B', description: 'Efficient general model · low VRAM' },
  { tag: 'mistral-nemo:12b', label: 'Mistral Nemo 12B', description: '128K context · strong multilingual' },
  { tag: 'mixtral:8x7b', label: 'Mixtral 8x7B', description: 'MoE · high quality · needs ~28GB VRAM' },
  // — General (Google Gemma) —
  { tag: 'gemma2:9b', label: 'Gemma 2 9B', description: "Google's open model · good general use" },
  { tag: 'gemma2:27b', label: 'Gemma 2 27B', description: 'Larger Gemma 2 · needs ~18GB VRAM' },
  { tag: 'gemma3:12b', label: 'Gemma 3 12B', description: 'Latest Gemma · multimodal-capable' },
  // — Small / specialty —
  { tag: 'phi3:3.8b', label: 'Phi-3 Mini 3.8B', description: 'Tiny but capable · low VRAM' },
  { tag: 'granite3.1-dense:8b', label: 'Granite 3.1 8B', description: "IBM's enterprise model · tool-use tuned" },
]

/** Turn a roster entry into a picker ModelOption. */
function toOption(e: OllamaRosterEntry, installed: boolean): ModelOption {
  return {
    value: e.tag,
    label: e.label,
    description: `${e.description}${installed ? ' · installed' : ''}`,
    descriptionForModel: `${e.label} (Ollama tag ${e.tag}) — ${e.description}`,
  }
}

/**
 * Names of models currently pulled into the local Ollama server, cached during
 * bootstrap (see services/api/bootstrap.ts). Set to a Set of tag strings once
 * fetched; left undefined when the fetch hasn't run or failed, in which case we
 * simply don't annotate installed state.
 */
let installedTags: Set<string> | undefined

export function setInstalledOllamaTags(tags: string[]): void {
  installedTags = new Set(tags)
}

/**
 * Build the `/model` picker options for the Ollama backend.
 *
 * Order: a "Default" entry (honors OLLAMA_MODEL / stock default), then any
 * installed models first, then the rest of the curated roster. The currently
 * configured OLLAMA_MODEL is always present even if it isn't in the roster.
 */
export function getOllamaModelOptions(): ModelOption[] {
  const configured = process.env.OLLAMA_MODEL || 'llama3.1:latest'

  const defaultOption: ModelOption = {
    value: null,
    label: 'Default (recommended)',
    description: `Use the configured Ollama model (currently ${configured})`,
    descriptionForModel: `Default Ollama model (currently ${configured})`,
  }

  // Split roster into installed-first, keeping curated order within each group.
  const isInstalled = (tag: string) => installedTags?.has(tag) ?? false
  const installed = OLLAMA_MODEL_ROSTER.filter(e => isInstalled(e.tag))
  const rest = OLLAMA_MODEL_ROSTER.filter(e => !isInstalled(e.tag))

  const options: ModelOption[] = [
    defaultOption,
    ...installed.map(e => toOption(e, true)),
    ...rest.map(e => toOption(e, false)),
  ]

  // Surface any installed model that isn't in the curated roster, so the user's
  // real inventory is always selectable.
  if (installedTags) {
    for (const tag of installedTags) {
      if (!tag.includes(':')) continue
      if (options.some(o => o.value === tag)) continue
      options.push({
        value: tag,
        label: tag,
        description: 'Installed Ollama model · installed',
        descriptionForModel: `Installed Ollama model (${tag})`,
      })
    }
  }

  // Guarantee the configured model is present and selectable.
  if (configured.includes(':') && !options.some(o => o.value === configured)) {
    options.push({
      value: configured,
      label: configured,
      description: 'Configured via OLLAMA_MODEL',
      descriptionForModel: `Configured Ollama model (${configured})`,
    })
  }

  return options
}
