"""Root-installed entry; never import from cwd, HOME or caller PYTHONPATH."""

import sys

sys.path.insert(0, "/usr/local/libexec/jarvis-autonomy-broker")

from tools.autonomy_broker_runtime import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
