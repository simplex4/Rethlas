#!/bin/sh
# Added in 2026 as the portable Rethlas ChatGPT MCP launcher.
set -eu
workflow_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$workflow_root"
exec "$workflow_root/.venv-chatgpt/bin/python" -m chatgpt_workflow.server "$@"
