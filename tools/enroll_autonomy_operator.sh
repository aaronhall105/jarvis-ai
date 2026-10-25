#!/usr/bin/env bash
set -euo pipefail
export PATH=/usr/sbin:/usr/bin:/sbin:/bin
exec /usr/bin/python3 -I "$(dirname -- "$(realpath -- "$0")")/enroll_autonomy_operator.py" "$@"
