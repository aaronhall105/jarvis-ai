# Natural response boundary

Jarvis treats connector, tool, database and provider output as evidence. User-facing
wording is produced separately and must preserve the evidence's truth and uncertainty.

## Boundary

`bridge/app/response_presentation.py` is the deterministic presentation policy. It does
not call a language model. Core request results pass through it in `main.py`; AI replies
also pass through it before conversation storage. Conversation storage applies the same
policy as a final safety boundary. Explicit requests for raw or technical detail remain
available for interactive responses, but scheduled and proactive delivery never exposes
internal diagnostics.

Structured Gmail reply evidence has a dedicated renderer. Reply existence and message
selection remain provider decisions; only the selected inbound body's presentation is
cleaned. Plain text is preferred over HTML, and quoted history, common mobile signatures,
HTML markup and entities are removed from the spoken text.

## Audited egress paths

| Path | Presentation enforcement |
| --- | --- |
| Android text and realtime voice | Core result in `main.py`, then `realtime_voice.py` |
| Wear OS | Receives the already-presented Android/Core completion |
| Home Assistant conversation | Receives the presented Core result; local transport errors use conversational wording |
| Conversation history | `ConversationEngine.add_assistant_message` |
| Gmail deterministic replies | `render_gmail_reply_status` |
| Follow-ups and external monitors | `FollowUpEngine._queue_delivery` and `_deliver_pending` |
| Proactive alerts | `ProactiveOrchestrator._deliver` and escalation/forward paths |
| House-awareness announcements | `HouseAwarenessEngine._maybe_deliver_proactive` |
| Scheduled and recurring completion notifications | task and recurring schedule engines |
| Conditional-action notifications | conditional action engine |

Tool call payloads, action receipts, logs and API diagnostics remain structured and
unchanged for auditability. The presentation policy affects user-visible text only.

## Authorization boundary

Presentation and referent resolution do not grant authority. Gmail reply-status history
is passed separately into a read-only resolver. The immutable current original request
remains the only authorization input for Gmail and calendar writes.
