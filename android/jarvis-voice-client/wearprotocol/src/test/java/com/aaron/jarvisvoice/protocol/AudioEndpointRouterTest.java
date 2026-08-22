package com.aaron.jarvisvoice.protocol;

import static org.junit.Assert.*;
import org.junit.Test;

public class AudioEndpointRouterTest {
    private static final class CountingSink implements AudioEndpointRouter.Sink { int frames; int interrupts; public void enqueue(byte[] pcm, long generation) { frames++; } public void interrupt() { interrupts++; } }
    @Test public void watchNeverPlaysOnPhoneAndPhoneBehaviourIsPreserved() {
        CountingSink phone = new CountingSink(), watch = new CountingSink(); AudioEndpointRouter router = new AudioEndpointRouter(phone, watch);
        router.begin(VoiceEndpoint.WATCH, 4); router.enqueue(new byte[]{1}, 4); assertEquals(0, phone.frames); assertEquals(1, watch.frames); assertEquals(VoiceEndpoint.WATCH, router.endpoint());
        router.begin(VoiceEndpoint.PHONE, 5); router.enqueue(new byte[]{1}, 5); assertEquals(1, phone.frames); assertEquals(1, watch.frames);
    }
    @Test public void cancellationDropsStaleAudio() {
        CountingSink phone = new CountingSink(), watch = new CountingSink(); AudioEndpointRouter router = new AudioEndpointRouter(phone, watch);
        router.begin(VoiceEndpoint.WATCH, 8); router.interrupt(); router.enqueue(new byte[]{1}, 8); assertEquals(0, watch.frames); assertFalse(router.active());
        router.begin(VoiceEndpoint.WATCH, 9); router.enqueue(new byte[]{1}, 8); assertEquals(0, watch.frames); assertTrue(watch.interrupts >= 2);
    }

    @Test public void watchEndpointPersistsAcrossFollowUpFrames() {
        CountingSink phone = new CountingSink(), watch = new CountingSink();
        AudioEndpointRouter router = new AudioEndpointRouter(phone, watch);
        router.begin(VoiceEndpoint.WATCH, 42);
        router.enqueue(new byte[]{1}, 42);
        router.enqueue(new byte[]{2}, 42);
        assertEquals(VoiceEndpoint.WATCH, router.endpoint());
        assertEquals(0, phone.frames);
        assertEquals(2, watch.frames);
    }

    @Test public void newlyOwnedTurnRearmsOutputAfterCancellation() {
        CountingSink phone = new CountingSink(), watch = new CountingSink();
        AudioEndpointRouter router = new AudioEndpointRouter(phone, watch);
        router.begin(VoiceEndpoint.WATCH, 42);
        router.interrupt();
        router.enqueue(new byte[]{1}, 42);
        assertEquals(0, watch.frames);
        router.resume(VoiceEndpoint.WATCH, 42);
        router.enqueue(new byte[]{2}, 42);
        assertEquals(1, watch.frames);
        assertEquals(0, phone.frames);
    }
}
