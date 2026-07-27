from pathlib import Path
import re

root = Path(__file__).resolve().parents[1]
source_path = root / "android/jarvis-voice-client/app/src/main/java/com/aaron/jarvisvoice/TranscriptPolicy.java"
source = source_path.read_text()

# Verify the Java source still contains the safety gates relied on by the client.
required_fragments = (
    "assistant_self_echo",
    "wake_required_during_playback",
    "wake_required",
    "wake_only",
    "stop_phrase",
    "expected_follow_up",
    "addressed",
    "looksLikeSelfEcho",
)
for fragment in required_fragments:
    assert fragment in source, fragment

match = re.search(r"STOP_PHRASES\s*=.*?Arrays\.asList\((.*?)\)\);", source, re.S)
assert match, "STOP_PHRASES declaration not found"
stop_phrases = set(re.findall(r'"([^"]+)"', match.group(1)))
for phrase in ("stop", "stop talking", "stop listening", "be quiet", "never mind", "forget it"):
    assert phrase in stop_phrases, phrase


def normalise(value: str | None) -> str:
    if value is None:
        return ""
    value = value.lower().replace("’", "'")
    value = re.sub(r"[^a-z0-9' ]", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def strip_wake_prefix(text: str, wake: str) -> str:
    for prefix in (wake, f"hey {wake}", f"okay {wake}", f"ok {wake}"):
        if text == prefix:
            return ""
        if text.startswith(prefix + " "):
            return text[len(prefix):].strip()
    return text


def looks_like_self_echo(transcript: str, speech: str) -> bool:
    heard = normalise(transcript)
    spoken = normalise(speech)
    if len(heard.split()) < 3 or len(spoken.split()) < 3:
        return False
    if heard in spoken or spoken in heard:
        return True
    a, b = set(heard.split()), set(spoken.split())
    smaller = min(len(a), len(b))
    return smaller > 0 and len(a & b) / smaller >= 0.80


def evaluate(transcript: str, speech: str, follow: bool, speaking: bool, wake_phrase: str = "jarvis") -> str:
    heard = normalise(transcript)
    if not heard:
        return "IGNORE"
    wake = normalise(wake_phrase) or "jarvis"
    command = strip_wake_prefix(heard, wake)
    addressed = command != heard
    if speaking and not addressed:
        if looks_like_self_echo(heard, speech):
            return "IGNORE"
        return "IGNORE"
    if not speaking and not follow and not addressed:
        return "IGNORE"
    if addressed and not command:
        return "IGNORE"
    accepted = command if addressed else heard
    if accepted in stop_phrases:
        return "STOP"
    if len(accepted.split()) > 45:
        return "IGNORE"
    return "COMMAND"

cases = (
    ("COMMAND", "Jarvis turn the light off", False, False),
    ("COMMAND", "Hey Jarvis open YouTube", False, False),
    ("IGNORE", "turn the light off", False, False),
    ("COMMAND", "the bedroom", True, False),
    ("IGNORE", "turn it off", False, True),
    ("STOP", "Jarvis stop", False, True),
    ("STOP", "Jarvis be quiet", False, True),
    ("COMMAND", "Jarvis turn both lights off", False, True),
    ("IGNORE", "The bedroom and hallway lights are on", False, True),
    ("IGNORE", "Jarvis", False, False),
    ("STOP", "never mind", True, False),
    ("COMMAND", "yes", True, False),
)
for expected, text, follow, speaking in cases:
    actual = evaluate(text, "The bedroom and hallway lights are on", follow, speaking)
    assert actual == expected, (text, actual, expected)

print({"policy_contract_tests": len(cases), "status": "ok"})
