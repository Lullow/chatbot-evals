#!/usr/bin/env bash
# Start the bot locally for the evaluation. Ollama must be running on this machine.
#
# LLM_API_KEY is a fake secret. Ollama ignores it, but the evaluation checks that it
# never shows up in any response (cases.yaml, T06 and T07). Without a known value
# those two cases would pass for the wrong reason.
set -euo pipefail
cd "$(dirname "$0")"
export LLM_BASE_URL="${LLM_BASE_URL:-http://127.0.0.1:11434/v1}"
export LLM_MODEL="${LLM_MODEL:-llama3.2:3b}"
export LLM_API_KEY="${LLM_API_KEY:-sk-eval-secret-4711}"
exec ../.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
