import asyncio
from pathlib import Path

import pytest

from developer_gateway.codex_client import APP_SERVER_STREAM_LIMIT, CodexAppServer


@pytest.mark.asyncio
async def test_app_server_accepts_json_lines_larger_than_asyncio_default(tmp_path: Path) -> None:
    executable = tmp_path / "large-app-server"
    executable.write_text(
        """#!/usr/bin/env python3
import json, sys
for line in sys.stdin:
    request = json.loads(line)
    if 'id' in request:
        print(json.dumps({'id': request['id'], 'result': {'padding': 'x' * 131072}}), flush=True)
""",
        encoding="utf-8",
    )
    executable.chmod(0o700)
    server = CodexAppServer(str(executable))
    try:
        await asyncio.wait_for(server.start(), 3)
        assert server.healthy
        assert APP_SERVER_STREAM_LIMIT > 131072
    finally:
        await server.stop()
