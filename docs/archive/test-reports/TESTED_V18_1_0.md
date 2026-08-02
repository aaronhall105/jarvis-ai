# Jarvis v18.1.0 validation

Completed before packaging:

- Python compilation for Core proxy, patcher and release tests.
- Core voice tests for v18.1.0 and Core application v3.1.0.
- Patch simulations from both v17.3.0 and v18.0.0 layouts.
- Android source contracts for `VoiceInteractionService`, session service, compact overlay, recognition service, role request, white theme and persistent wake settings.
- Release-layout and workflow validation.
- Dependency-free Java contracts retained from the chat and voice product.
- Shell syntax validation.
- No Home Assistant integration files included.

Still required on Aaron's phone:

- GitHub Actions Android SDK 36 compilation.
- Selection as the default Digital assistant app.
- Samsung Side-button invocation test.
- Overlay placement and keyboard test on the installed One UI version.
- Screen-off wake-phrase reliability test with battery usage set to Unrestricted.

The release does not claim a dedicated DSP hotword engine; wake listening uses Android's on-device recogniser.
