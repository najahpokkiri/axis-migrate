#!/bin/sh
# Provision the fenced eval user `axis-eval` (cannot read /home/migrate): Claude Code 2.1.296 and uv in its
# ~/.local/bin, the eval harness env in ~/harness-env. Idempotent. Run as migrate.
set -eu
DROP=/tmp/axis-eval-drop
mkdir -p "$DROP"; chmod 755 "$DROP"
CLAUDE_BIN="$(readlink -f "$HOME/.local/bin/claude")"
cp -f "$CLAUDE_BIN" "$DROP/claude"; cp -f "$HOME/.local/bin/uv" "$DROP/uv"
cp -f "$(dirname "$0")/harness-env/pyproject.toml" "$(dirname "$0")/harness-env/uv.lock" "$DROP/"
chmod 755 "$DROP/claude" "$DROP/uv"; chmod 644 "$DROP/pyproject.toml" "$DROP/uv.lock"
sudo -n -u axis-eval sh -c '
  set -eu; mkdir -p ~/.local/bin ~/harness-env ~/ws ~/plugins
  cmp -s /tmp/axis-eval-drop/claude ~/.local/bin/claude || cp /tmp/axis-eval-drop/claude ~/.local/bin/claude
  cmp -s /tmp/axis-eval-drop/uv ~/.local/bin/uv || cp /tmp/axis-eval-drop/uv ~/.local/bin/uv
  cp /tmp/axis-eval-drop/pyproject.toml /tmp/axis-eval-drop/uv.lock ~/harness-env/
  cd ~/harness-env && UV_PYTHON_PREFERENCE=only-managed ~/.local/bin/uv sync -q --frozen
  ~/.local/bin/claude --version; ~/harness-env/.venv/bin/python -c "import claude_agent_sdk; print(\"sdk ok\")"'
