#!/bin/bash
set -euo pipefail

# installs system packages for pre-commit hooks (remote only)
if [[ ${CLAUDE_CODE_REMOTE:-} == true ]] && ! command -v shellcheck &> /dev/null; then
  if ! (apt-get update -qq && apt-get install -y -qq shellcheck) 2>&1; then
    echo "WARNING: failed to install shellcheck. shell linting hooks will not work." >&2
  fi
fi

# creates venv if it doesn't exist
if [[ ! -d venv ]]; then
  python3 -m venv venv

  # shellcheck source=/dev/null
  source venv/bin/activate

  pip install -q -e ".[dev]"
else
  # shellcheck source=/dev/null
  source venv/bin/activate
fi

# installs pre-commit hooks (fast if already installed)
pre-commit install
pre-commit install --hook-type commit-msg

# activates the venv for the session
# shellcheck disable=SC2016
echo 'source "$CLAUDE_PROJECT_DIR/venv/bin/activate"' >> "$CLAUDE_ENV_FILE"
