# Privacy

Jarvis Voice stores the configured Home Assistant access token encrypted with Android Keystore. Speech recognition is requested through the Android speech-recognition service available on the device. Depending on the selected recognition service and installed language models, recognition may be performed on-device or by that service's provider. Accepted command text is sent only to the configured Home Assistant instance.
