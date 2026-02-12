# macOS arm64 patched Codex binary verification

This document records the verified local binary used for MCP auto-reconnect testing on macOS arm64.

## Binary path

`/opt/homebrew/lib/node_modules/@openai/codex/vendor/aarch64-apple-darwin/codex/codex`

## Verified result

- Verification date: 2026-02-12
- `codex --version`: `codex-cli 9.9.99`
- SHA-256:
  `f470c425026e10e7103dd3b60dde3fef9b1a6aaf677100327732a6261a8c4e6d`

## Re-check commands

```bash
/opt/homebrew/lib/node_modules/@openai/codex/vendor/aarch64-apple-darwin/codex/codex --version
shasum -a 256 /opt/homebrew/lib/node_modules/@openai/codex/vendor/aarch64-apple-darwin/codex/codex
```

Expected output:

```text
codex-cli 9.9.99
f470c425026e10e7103dd3b60dde3fef9b1a6aaf677100327732a6261a8c4e6d  /opt/homebrew/lib/node_modules/@openai/codex/vendor/aarch64-apple-darwin/codex/codex
```

## Notes

- This is a fork-maintained build intended to include local MCP reconnect improvements.
- If either version or hash changes, update this document and re-run MCP reconnect verification.
