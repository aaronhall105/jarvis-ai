# Jarvis v19.0.0-alpha13

## Final Polish, Persistent Wake and Adaptive Replies

- Keeps the dedicated offline wake detector independent of Jarvis Core
  connectivity and repairs a stopped detector with bounded retries and a
  quiet watchdog.
- Re-arms wake after a closing phrase, Core reconnect and removal of the
  full app from recent tasks without overlapping detector restarts.
- Uses a separate silent, low-priority Android wake-word notification
  channel with direct settings access.
- Selects output ceilings from the request so controls stay compact while
  stories and detailed answers are no longer cut off by one fixed limit.
- Streams full text to chat while speech starts from the first complete
  useful sentence. Long replies speak a bounded excerpt.
- Replaces the crowded five-button toolbar with House activity, New chat
  and one overflow menu.
- Reorganises Settings into Voice, Wake, Assistant, Connections and
  Diagnostics.
- Retains every Alpha5 through Alpha12 regression and adds Alpha13 tests.
