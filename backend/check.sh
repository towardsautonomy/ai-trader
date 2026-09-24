#!/usr/bin/env bash
# The one command to run before trusting a change: full regression suite.
#   ./check.sh          everything
#   ./check.sh -k kill  a subset (args pass through to pytest)
set -euo pipefail
cd "$(dirname "$0")"
uv run pytest tests -q -W error::pytest.PytestUnhandledThreadExceptionWarning "$@"
