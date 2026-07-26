"""Constants for the Jarvis Core Conversation integration."""

DOMAIN = "jarvis_core_conversation"

CONF_URL = "url"
CONF_TIMEOUT = "timeout"
CONF_FOLLOW_UP_MODE = "follow_up_mode"

DEFAULT_URL = "http://192.168.1.40:8000"
DEFAULT_TIMEOUT = 60

FOLLOW_UP_ALWAYS = "always"
FOLLOW_UP_QUESTIONS = "questions"
FOLLOW_UP_DISABLED = "disabled"
FOLLOW_UP_MODES = (
    FOLLOW_UP_ALWAYS,
    FOLLOW_UP_QUESTIONS,
    FOLLOW_UP_DISABLED,
)
DEFAULT_FOLLOW_UP_MODE = FOLLOW_UP_ALWAYS
