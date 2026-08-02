# Jarvis v17.1.0-r1 validation

Validated in the release environment:

- 12 dependency-free transcript-policy contract tests pass;
- 12 compiled Java transcript-policy tests pass when Java is available;
- release package and required-file validation pass;
- installer succeeds when `javac` and `java` are deliberately hidden from `PATH`;
- installer shell syntax passes;
- Android manifest permissions and foreground microphone service are checked;
- no access token or user credential is embedded in source;
- no Docker rebuild or Home Assistant modification is performed.

The Android APK itself remains subject to the included GitHub Actions Android SDK build and real-device validation.
