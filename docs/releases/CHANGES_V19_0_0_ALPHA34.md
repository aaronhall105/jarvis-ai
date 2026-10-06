# Jarvis 19.0.0-alpha34

This release makes Chat and Tasks feel like two views inside one stable Android
product shell and keeps the typed composer ready for consecutive messages.

## Shared Android shell

- Chat and Tasks now use one header implementation with identical system
  insets, wordmark, assistant context, action row, and primary navigation.
- Switching between Chat and Tasks changes only the body and uses no competing
  Activity transition animation.
- Shared spacing, radius, colour, touch-target, and navigation tokens replace
  screen-specific geometry.
- Top actions retain accessible touch targets while using lighter visual
  circles, subtle borders, and no heavy elevation.

## Tasks presentation

- The stable `important_only:inbox` task is presented to users as **Smart
  Inbox**; internal IDs, policy names, database keys, and APIs are unchanged.
- Monitoring is shown in a compact card with one historical cleanup summary,
  lightweight provider rows, and a clear details affordance.
- Sync freshness remains page-level rather than being repeated on every card.
- Four equal-width filters remain visible at Galaxy S21+ and narrower supported
  phone widths.

## Chat input

- Button and IME sends clear the text but retain input focus and request that
  the soft keyboard remain visible.
- Streaming message updates scroll by coordinates rather than focus-moving
  operations, so the composer is not displaced while the user types.
- Back, voice entry, and navigation away from Chat retain intentional keyboard
  dismissal behavior.

## Compatibility

- Phone and Wear share versionCode `190360` and realtime protocol `2`.
- The update uses the existing production signing identity and installs in
  place without clearing app data, authentication, conversations, or endpoint
  preferences.
- Important-Only classification, authority, checkpoints, and mailbox execution
  semantics are unchanged.
