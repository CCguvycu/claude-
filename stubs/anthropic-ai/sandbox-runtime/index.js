// Stub for @anthropic-ai/sandbox-runtime
//
// The real package provides OS-level sandboxing (macOS Seatbelt / Linux
// bubblewrap). This build ships without it, so the stub must faithfully report
// "no sandbox available" for every static method sandbox-adapter.ts calls.
//
// IMPORTANT: sandbox-adapter.ts calls these as STATIC methods on the class
// (e.g. `SandboxManager.isSupportedPlatform()`). If any are missing, the call
// site invokes `undefined()`, the async startup chain rejects, and — because
// stdin/MCP sockets keep the event loop alive — the process hangs instead of
// exiting. So every referenced static is defined here with a safe default.

export class SandboxManager {
  constructor() {}

  // --- platform / dependency probes -------------------------------------
  // Report unsupported so isSandboxingEnabled() short-circuits to false and
  // never attempts to wrap commands.
  static isSupportedPlatform() {
    return false
  }
  static checkDependencies() {
    return {
      errors: ['sandbox-runtime is not bundled in this build'],
      warnings: [],
    }
  }

  // --- lifecycle (no-ops; never reached while unsupported) --------------
  static initialize() {
    return Promise.resolve()
  }
  static updateConfig() {}
  static reset() {}
  static waitForNetworkInitialization() {
    return Promise.resolve()
  }
  static cleanupAfterCommand() {}

  // --- command wrapping: pass the command through unchanged -------------
  static wrapWithSandbox(command /* , ...rest */) {
    return command
  }
  static annotateStderrWithSandboxFailures(stderr) {
    return stderr
  }

  // --- config getters: return permissive/empty defaults -----------------
  static getFsReadConfig() {
    return undefined
  }
  static getFsWriteConfig() {
    return undefined
  }
  static getNetworkRestrictionConfig() {
    return undefined
  }
  static getIgnoreViolations() {
    return false
  }
  static getAllowUnixSockets() {
    return true
  }
  static getAllowLocalBinding() {
    return true
  }
  static getEnableWeakerNestedSandbox() {
    return false
  }
  static getProxyPort() {
    return undefined
  }
  static getSocksProxyPort() {
    return undefined
  }
  static getLinuxHttpSocketPath() {
    return undefined
  }
  static getLinuxSocksSocketPath() {
    return undefined
  }
  static getSandboxViolationStore() {
    return new SandboxViolationStore()
  }

  // --- instance methods (legacy) ----------------------------------------
  start() {
    return Promise.resolve()
  }
  stop() {
    return Promise.resolve()
  }
}

export const SandboxRuntimeConfigSchema = {}

export class SandboxViolationStore {
  constructor() {}
  getViolations() {
    return []
  }
}
