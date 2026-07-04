// ARK: ensure the ripgrep binary lives where the CLI expects it.
//
// The upstream leak ships WITHOUT a vendored rg.exe at
// src/utils/vendor/ripgrep/<arch>-<platform>/. Without it, `getRipgrepConfig()`
// resolves to a non-existent path and EVERY ripgrep call throws — which silently
// breaks custom-command/skill discovery (~/.claude/commands/*.md never load) and
// the Grep tool. The claude-agent-sdk dependency bundles a real rg, so we copy it
// into both the source tree (for `bun src` runs) and dist/ (for the bundled cli.js,
// whose __dirname resolves to dist/). Run automatically after `ark:build`.

import { copyFileSync, mkdirSync, existsSync } from 'fs'
import { dirname, join } from 'path'
import { fileURLToPath } from 'url'

const REPO = join(dirname(fileURLToPath(import.meta.url)), '..')

const isWin = process.platform === 'win32'
const rgName = isWin ? 'rg.exe' : 'rg'
const platDir = `${process.arch}-${isWin ? 'win32' : process.platform}`

const SRC = join(
  REPO,
  'node_modules',
  '@anthropic-ai',
  'claude-agent-sdk',
  'vendor',
  'ripgrep',
  platDir,
  rgName,
)

if (!existsSync(SRC)) {
  console.error(`[ark-vendor-ripgrep] source rg not found: ${SRC}`)
  console.error('[ark-vendor-ripgrep] run `bun install` first. Skipping.')
  process.exit(0)
}

const targets = [
  join(REPO, 'src', 'utils', 'vendor', 'ripgrep', platDir, rgName),
  join(REPO, 'dist', 'vendor', 'ripgrep', platDir, rgName),
]

for (const dst of targets) {
  try {
    mkdirSync(dirname(dst), { recursive: true })
    copyFileSync(SRC, dst)
    console.error(`[ark-vendor-ripgrep] -> ${dst}`)
  } catch (e) {
    console.error(`[ark-vendor-ripgrep] failed ${dst}: ${e?.message || e}`)
  }
}
