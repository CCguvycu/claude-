// arkMacro — runtime provider for the build-time `MACRO` constants.
//
// When the CLI is bundled (or run from the repo via bunfig.toml's [define]),
// `MACRO.VERSION` etc. are replaced at transpile time. But `arkcli` runs the
// entry from an arbitrary working directory (so the agent operates on YOUR
// folder, not the repo), which means the repo's bunfig.toml — and its defines —
// aren't loaded. In that case `MACRO` is just a global identifier lookup, so we
// populate it here as a preload before the entry module runs.
;(globalThis as unknown as { MACRO: Record<string, unknown> }).MACRO = {
  VERSION: '1.0.0-dev',
  BUILD_TIME: '',
  PACKAGE_URL: '@anthropic-ai/claude-code',
  NATIVE_PACKAGE_URL: undefined,
  FEEDBACK_CHANNEL: 'https://github.com/anthropics/claude-code/issues',
  ISSUES_EXPLAINER:
    'file an issue at https://github.com/anthropics/claude-code/issues',
  VERSION_CHANGELOG: '',
}
