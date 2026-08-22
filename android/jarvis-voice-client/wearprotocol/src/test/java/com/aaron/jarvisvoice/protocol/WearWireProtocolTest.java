package com.aaron.jarvisvoice.protocol;

import static org.junit.Assert.*;
import java.io.ByteArrayInputStream;
import java.io.ByteArrayOutputStream;
import java.io.DataOutputStream;
import java.io.IOException;
import org.junit.Test;

public class WearWireProtocolTest {
    @Test public void frameRoundTripPreservesTypeGenerationAndAudio() throws Exception {
        ByteArrayOutputStream output = new ByteArrayOutputStream();
        WearWireProtocol.write(output, WearWireProtocol.MIC_AUDIO, 91L, new byte[]{1, 2, 3});
        WearWireProtocol.Frame frame = WearWireProtocol.read(
            new ByteArrayInputStream(output.toByteArray())
        );
        assertEquals(WearWireProtocol.MIC_AUDIO, frame.type());
        assertEquals(91L, frame.generation());
        assertArrayEquals(new byte[]{1, 2, 3}, frame.payload());
    }

    @Test public void oversizedInboundFrameIsRejectedBeforeAllocation() throws Exception {
        ByteArrayOutputStream output = new ByteArrayOutputStream();
        DataOutputStream data = new DataOutputStream(output);
        data.writeByte(WearWireProtocol.OUTPUT_AUDIO);
        data.writeLong(1L);
        data.writeInt(WearWireProtocol.MAX_FRAME_BYTES + 1);
        assertThrows(
            IOException.class,
            () -> WearWireProtocol.read(new ByteArrayInputStream(output.toByteArray()))
        );
    }
}
