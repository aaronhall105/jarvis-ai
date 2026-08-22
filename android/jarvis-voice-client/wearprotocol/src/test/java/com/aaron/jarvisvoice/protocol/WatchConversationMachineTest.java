package com.aaron.jarvisvoice.protocol;

import static org.junit.Assert.*;
import org.junit.Test;

public class WatchConversationMachineTest {
    @Test public void idleToListening() {
        WatchConversationMachine machine = new WatchConversationMachine();
        machine.start();
        assertEquals(WatchConversationState.LISTENING, machine.state());
    }

    @Test public void listeningToProcessing() {
        WatchConversationMachine machine = new WatchConversationMachine();
        long generation = machine.start();
        assertTrue(machine.processing(generation));
        assertEquals(WatchConversationState.PROCESSING, machine.state());
    }

    @Test public void processingToSpeaking() {
        WatchConversationMachine machine = new WatchConversationMachine();
        long generation = machine.start();
        machine.processing(generation);
        assertTrue(machine.speaking(generation));
        assertEquals(WatchConversationState.SPEAKING, machine.state());
    }

    @Test public void speakingAutomaticallyReturnsToListening() {
        WatchConversationMachine machine = new WatchConversationMachine();
        long generation = machine.start();
        machine.processing(generation);
        machine.speaking(generation);
        assertTrue(machine.playbackComplete(generation));
        assertEquals(WatchConversationState.LISTENING, machine.state());
    }

    @Test public void firstAudioCanEstablishSpeakingBeforeStateFrame() {
        WatchConversationMachine machine = new WatchConversationMachine();
        long generation = machine.start();
        assertTrue(machine.speaking(generation));
        assertEquals(WatchConversationState.SPEAKING, machine.state());
        assertTrue(machine.playbackComplete(generation));
        assertEquals(WatchConversationState.LISTENING, machine.state());
    }

    @Test public void normalContinuousFlow() {
        WatchConversationMachine machine = new WatchConversationMachine();
        long generation = machine.start(); assertEquals(WatchConversationState.LISTENING, machine.state());
        assertTrue(machine.processing(generation)); assertEquals(WatchConversationState.PROCESSING, machine.state());
        assertTrue(machine.speaking(generation)); assertEquals(WatchConversationState.SPEAKING, machine.state());
        assertTrue(machine.playbackComplete(generation)); assertEquals(WatchConversationState.LISTENING, machine.state());
        assertTrue(machine.processing(generation));
    }
    @Test public void xEndsFromEveryActiveState() {
        WatchConversationMachine listening = new WatchConversationMachine(); listening.start(); listening.end(); assertEquals(WatchConversationState.IDLE, listening.state());
        WatchConversationMachine processing = new WatchConversationMachine(); long p = processing.start(); processing.processing(p); processing.end(); assertEquals(WatchConversationState.IDLE, processing.state());
        WatchConversationMachine speaking = new WatchConversationMachine(); long s = speaking.start(); speaking.processing(s); speaking.speaking(s); speaking.end(); assertEquals(WatchConversationState.IDLE, speaking.state());
    }
    @Test public void timeoutAndDisconnectReturnIdle() {
        WatchConversationMachine timeout = new WatchConversationMachine(1234); timeout.start(); timeout.inactivityTimeout(); assertEquals(WatchConversationState.IDLE, timeout.state()); assertEquals(1234, timeout.inactivityTimeoutMs());
        WatchConversationMachine disconnected = new WatchConversationMachine(); disconnected.start(); disconnected.disconnect(); assertEquals(WatchConversationState.IDLE, disconnected.state());
    }
    @Test public void duplicateStartDoesNotReplaceSessionAndStaleFramesAreRejected() {
        WatchConversationMachine machine = new WatchConversationMachine(); long first = machine.start(); assertEquals(first, machine.start()); machine.end(); assertFalse(machine.accepts(first)); long second = machine.start(); assertNotEquals(first, second); assertFalse(machine.processing(first));
    }

    @Test public void generationsAreUniqueAcrossControllerInstances() {
        WatchConversationMachine first = new WatchConversationMachine();
        long old = first.start();
        first.end();
        WatchConversationMachine replacement = new WatchConversationMachine();
        long current = replacement.start();
        assertNotEquals(old, current);
        assertFalse(replacement.processing(old));
    }

    @Test public void microphoneIsOwnedOnlyByListeningStates() {
        assertFalse(WatchMicrophonePolicy.shouldCapture(WatchConversationState.IDLE));
        assertTrue(WatchMicrophonePolicy.shouldCapture(WatchConversationState.LISTENING));
        assertTrue(WatchMicrophonePolicy.shouldCapture(WatchConversationState.FOLLOW_UP));
        assertFalse(WatchMicrophonePolicy.shouldCapture(WatchConversationState.PROCESSING));
        assertFalse(WatchMicrophonePolicy.shouldCapture(WatchConversationState.SPEAKING));
        assertFalse(WatchMicrophonePolicy.shouldCapture(WatchConversationState.ENDING));
    }
}
