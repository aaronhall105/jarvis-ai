package com.aaron.jarvisvoice;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertThrows;

import org.junit.Test;

public final class AudioFrameSizerTest {
    @Test public void twentyMillisecondsAtTwentyFourKhzIsNineHundredSixtyBytes() {
        assertEquals(960, AudioFrameSizer.bytesFor(24_000, 20));
    }

    @Test public void invalidValuesFail() {
        assertThrows(IllegalArgumentException.class, () -> AudioFrameSizer.bytesFor(0, 20));
    }
}
