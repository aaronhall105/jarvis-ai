import tempfile
import unittest
from pathlib import Path

from app.code_awareness import CodeAwarenessEngine


class CodeAwarenessTests(
    unittest.IsolatedAsyncioTestCase
):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

        source = self.root / "src"
        source.mkdir()

        (source / "demo.py").write_text(
            "def example():\n"
            "    return 'needle-value'\n",
            encoding="utf-8",
        )

        (self.root / ".env").write_text(
            "SECRET=do-not-read\n",
            encoding="utf-8",
        )

        speaker_data = self.root / "speaker-data"
        speaker_data.mkdir()

        (speaker_data / "private.txt").write_text(
            "private voice material\n",
            encoding="utf-8",
        )

        self.engine = CodeAwarenessEngine(
            enabled=True,
            roots={
                "jarvis": self.root,
                "voice_pe": self.root / "missing",
            },
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    async def test_read_source_file(self) -> None:
        result = await self.engine.execute(
            "code_read",
            {
                "root": "jarvis",
                "path": "src/demo.py",
                "start_line": 1,
                "end_line": 20,
            },
        )

        self.assertTrue(result["success"])
        self.assertIn(
            "needle-value",
            result["content"],
        )

    async def test_secret_file_is_blocked(self) -> None:
        result = await self.engine.execute(
            "code_read",
            {
                "root": "jarvis",
                "path": ".env",
                "start_line": 1,
                "end_line": 20,
            },
        )

        self.assertFalse(result["success"])

    async def test_speaker_data_is_blocked(self) -> None:
        result = await self.engine.execute(
            "code_read",
            {
                "root": "jarvis",
                "path": "speaker-data/private.txt",
                "start_line": 1,
                "end_line": 20,
            },
        )

        self.assertFalse(result["success"])

    async def test_path_escape_is_blocked(self) -> None:
        result = await self.engine.execute(
            "code_read",
            {
                "root": "jarvis",
                "path": "../outside.py",
                "start_line": 1,
                "end_line": 20,
            },
        )

        self.assertFalse(result["success"])

    async def test_search_source(self) -> None:
        result = await self.engine.execute(
            "code_search",
            {
                "root": "jarvis",
                "path": "",
                "query": "needle-value",
                "max_results": 10,
            },
        )

        self.assertTrue(result["success"])
        self.assertEqual(result["count"], 1)
        self.assertEqual(
            result["matches"][0]["path"],
            "src/demo.py",
        )


if __name__ == "__main__":
    unittest.main()
