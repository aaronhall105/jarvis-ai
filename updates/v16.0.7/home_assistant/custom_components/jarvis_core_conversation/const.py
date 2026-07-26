"""Constants for the Jarvis Core Conversation integration."""

DOMAIN = "jarvis_core_conversation"

CONF_URL = "url"
CONF_TIMEOUT = "timeout"
CONF_FOLLOW_UP_MODE = "follow_up_mode"
CONF_SPOKEN_PROGRESS = "spoken_progress"
CONF_SHOW_PROGRESS_TEXT = "show_progress_text"
CONF_AUDIO_GATE_ENABLED = "audio_gate_enabled"
CONF_FOLLOW_UP_WINDOW = "follow_up_window_seconds"
CONF_AUDIO_GATE_MIGRATED = "audio_gate_migrated_v154"

DEFAULT_URL = "http://192.168.1.40:8000"
DEFAULT_TIMEOUT = 60

FOLLOW_UP_SMART = "smart"
FOLLOW_UP_ALWAYS = "always"
FOLLOW_UP_QUESTIONS = "questions"
FOLLOW_UP_DISABLED = "disabled"
FOLLOW_UP_MODES = (
    FOLLOW_UP_SMART,
    FOLLOW_UP_ALWAYS,
    FOLLOW_UP_QUESTIONS,
    FOLLOW_UP_DISABLED,
)
DEFAULT_FOLLOW_UP_MODE = FOLLOW_UP_SMART
DEFAULT_SPOKEN_PROGRESS = True
DEFAULT_SHOW_PROGRESS_TEXT = True
DEFAULT_AUDIO_GATE_ENABLED = True
DEFAULT_FOLLOW_UP_WINDOW = 12
