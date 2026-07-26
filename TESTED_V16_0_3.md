# Validation performed for v16.0.3

- Core task and progress unit tests passed.
- Home Assistant streaming-helper tests passed.
- Phrase library contains 105 acknowledgements.
- Phrase selection avoids repeating a category phrase until its full category pool has been cycled.
- Python compilation passed for every changed Python module.
- The Core progress patch accepts v16.0.1 and v16.0.2 layouts and refuses unexpected source.
- The Home Assistant integration uses the originating device registry entry to select its media player.
- The Home Assistant installer includes backup and rollback handling.
- The Jarvis-hosted Home Assistant update archive is validated by the Ubuntu installer.

Live playback on the Home Assistant Voice Preview hardware remains the final acceptance test because container unit tests cannot reproduce the physical satellite media player.
