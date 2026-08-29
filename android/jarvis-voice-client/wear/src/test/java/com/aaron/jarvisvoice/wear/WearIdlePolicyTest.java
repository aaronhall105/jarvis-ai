package com.aaron.jarvisvoice.wear;

import static org.junit.Assert.*;
import com.aaron.jarvisvoice.protocol.WatchConversationMachine;
import com.aaron.jarvisvoice.protocol.WatchConversationState;
import org.junit.Test;

public class WearIdlePolicyTest {
    @Test public void idleStateDoesNotImplyMicrophoneOwnership() {
        WatchConversationMachine machine = new WatchConversationMachine();
        assertEquals(WatchConversationState.IDLE, machine.state());
        assertFalse(machine.accepts(machine.generation()));
    }
}
