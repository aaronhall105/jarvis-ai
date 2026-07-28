# Jarvis Android v18.2.1

## Launch crash hotfix

- Fixed `MainActivity` crashing before its first frame was drawn.
- Delayed `WindowInsetsController` access until after the window's
  `DecorView` has been created and attached.
- Applied the same correction to `SettingsActivity`.
- Retained the v18.2.0 monochrome interface, keyboard Send action,
  microphone button, system-bar padding and wake-word changes.
