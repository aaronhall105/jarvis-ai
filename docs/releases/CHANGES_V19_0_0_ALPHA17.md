# Jarvis 19.0.0-alpha17

Alpha17 restores the configured ElevenLabs Jarvis voice on Android. The original voice selection now requests Core's direct ElevenLabs PCM stream instead of routing completed text back through a Home Assistant Assist pipeline or Android device TTS.

It also hardens loudspeaker echo fencing by normalizing Unicode apostrophes consistently with speech-recognizer output. This prevents Jarvis phrases containing contractions from being accepted as new user turns while preserving explicit wake-word and stop-command barge-in.

Core application version remains 3.7.0 and realtime protocol remains 2 because the existing authentication and PCM event contract is unchanged.
