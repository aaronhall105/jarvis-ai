package com.aaron.jarvisvoice.protocol;

import static org.junit.Assert.*;
import java.util.List;
import org.junit.Test;

public class StartupAudioBufferTest {
    @Test public void preservesFirstFramesInOrder() {
        StartupAudioBuffer buffer = new StartupAudioBuffer(12);
        buffer.begin(7L);
        assertTrue(buffer.offer(7L, new byte[] {1, 2}));
        assertTrue(buffer.offer(7L, new byte[] {3, 4}));
        List<byte[]> frames = buffer.drain(7L);
        assertArrayEquals(new byte[] {1, 2}, frames.get(0));
        assertArrayEquals(new byte[] {3, 4}, frames.get(1));
        assertEquals(0, buffer.sizeBytes());
    }

    @Test public void remainsBoundedByDroppingOldestAudio() {
        StartupAudioBuffer buffer = new StartupAudioBuffer(4);
        buffer.begin(8L);
        buffer.offer(8L, new byte[] {1, 2});
        buffer.offer(8L, new byte[] {3, 4});
        buffer.offer(8L, new byte[] {5, 6});
        List<byte[]> frames = buffer.drain(8L);
        assertEquals(2, frames.size());
        assertArrayEquals(new byte[] {3, 4}, frames.get(0));
        assertArrayEquals(new byte[] {5, 6}, frames.get(1));
    }

    @Test public void cancellationAndNewGenerationRejectStaleFrames() {
        StartupAudioBuffer buffer = new StartupAudioBuffer(8);
        buffer.begin(10L);
        buffer.offer(10L, new byte[] {1});
        buffer.cancel(10L);
        assertTrue(buffer.drain(10L).isEmpty());
        buffer.begin(11L);
        assertFalse(buffer.offer(10L, new byte[] {2}));
        assertTrue(buffer.drain(11L).isEmpty());
    }
}
