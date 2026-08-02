# Jarvis Android v18.4.2

- Returns to the dedicated offline Jarvis detector after a closing phrase.
- Closes the compact overlay after the final response.
- Leaves the full app open after the same ending.
- Uses VoiceService as the single background wake-word microphone owner.
- Makes Standard mode the recommended production speech mode.
- Uses the normal Android recogniser first for more accurate UK English.
- Extends silence windows so commands are not cut off.
- Automatically recovers from recognition timeouts.
- Keeps interruption listening active while thinking and speaking.
- Cancels and discards the active response when a real interruption is heard.
- Filters likely speaker echo so Jarvis does not interrupt itself.
- Supports polite endings such as "Okay goodbye" and "Thanks Jarvis".
