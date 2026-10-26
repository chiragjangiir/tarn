#!/bin/sh
# Shared native Linux release architecture detection. Never guess a fallback.
[ "$(uname -s)" = Linux ] || { echo 'Only Linux is supported' >&2; exit 1; }
case "$(uname -m)" in
  x86_64) printf '%s\n' x86_64 ;;
  aarch64|arm64) printf '%s\n' aarch64 ;;
  *) echo 'Only Linux x86_64 and ARM64 (aarch64) are supported' >&2; exit 1 ;;
esac
