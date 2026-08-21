import tempfile
import unittest
from pathlib import Path

from app.speech_corrections import SpeechCorrectionEngine


class SpeechCorrectionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.engine = SpeechCorrectionEngine(
            str(Path(self.directory.name) / "corrections.db")
        )

    async def asyncTearDown(self):
        self.directory.cleanup()

    async def test_explicit_registry_grounded_correction_is_learned(self):
        learned = await self.engine.learn_explicit(
            "No, I said TV, not G A P L E",
            "aaron",
            {"tv"},
        )
        self.assertEqual(learned, ("g a p l e", "tv"))
        corrected, applied = await self.engine.apply(
            "Turn off the G A P L E",
            "aaron",
        )
        self.assertEqual(corrected, "Turn off the tv")
        self.assertEqual(applied, ("g a p l e → tv",))

    async def test_unknown_correction_is_rejected(self):
        learned = await self.engine.learn_explicit(
            "When I say nursery lights, I mean moon cannon",
            "aaron",
            {"tv"},
        )
        self.assertIsNone(learned)

    async def test_corrections_are_user_scoped(self):
        await self.engine.learn_explicit(
            "When I say telly, I mean TV",
            "aaron",
            {"tv"},
        )
        corrected, _ = await self.engine.apply("Turn on the telly", "amber")
        self.assertEqual(corrected, "Turn on the telly")

    async def test_only_destination_is_reused_as_prompt_vocabulary(self):
        await self.engine.learn_explicit(
            "When I say telly, I mean TV",
            "aaron",
            {"tv"},
        )
        self.assertEqual(await self.engine.prompt_terms("aaron"), ("tv",))
        self.assertEqual(await self.engine.prompt_terms("amber"), ())
        self.assertEqual(await self.engine.prompt_terms("guest"), ("tv",))

    async def test_privacy_listing_and_forgetting_are_user_scoped(self):
        await self.engine.learn_explicit(
            "When I say telly, I mean TV", "aaron", {"tv"}
        )
        self.assertEqual(
            await self.engine.list_for_user("aaron"),
            ({"heard_as": "telly", "means": "tv"},),
        )
        self.assertEqual(await self.engine.list_for_user("amber"), ())
        self.assertEqual(await self.engine.forget("aaron", "telly"), 1)
        self.assertEqual(await self.engine.list_for_user("aaron"), ())


if __name__ == "__main__":
    unittest.main()
