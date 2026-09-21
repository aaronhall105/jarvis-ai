# Jarvis 19.0.0-alpha29 — safer multi-inbox cleanup

- Understands compound Gmail and Outlook cleanup requests while keeping each account's candidates and receipts separate.
- Preserves “all my inboxes” across follow-up turns and freezes the exact matching messages before asking for confirmation.
- Warns when a one-off cleanup includes old unread mail that may be personal or important.
- Uses Gmail Bin and Outlook Deleted Items for recoverable cleanup; permanent deletion is not available.
- Reports partial provider failures truthfully instead of presenting them as a Jarvis Core transport error.
- Keeps routine phone interruptions under the Smart Important Only notification policy introduced since alpha28.
- Includes the optional Astra Executive Agent routing and safety architecture already deployed in Core.
