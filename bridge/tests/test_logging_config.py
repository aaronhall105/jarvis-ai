from __future__ import annotations

import logging
from unittest.mock import patch

from app import logging_config


def test_http_client_request_urls_are_not_logged_at_info() -> None:
    levels: dict[str, int] = {}

    class _Logger:
        def __init__(self, name: str) -> None:
            self.name = name

        def setLevel(self, level: int) -> None:
            levels[self.name] = level

    with (
        patch.object(logging_config.logging, "basicConfig"),
        patch.object(logging_config.logging, "getLogger", side_effect=_Logger),
    ):
        logging_config.configure_logging("INFO")

    assert levels == {"httpx": logging.WARNING, "httpcore": logging.WARNING}
