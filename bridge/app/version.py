"""Authoritative Jarvis product release identity.

Keep product release, Core application API identity and protocol revision in one
small dependency-free module so backend components and tests do not pin old
candidate versions independently.
"""

JARVIS_RELEASE = "19.0.0-alpha21"
CORE_APPLICATION_VERSION = "3.7.0"
REALTIME_PROTOCOL_VERSION = 2
