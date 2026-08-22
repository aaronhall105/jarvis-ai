package com.aaron.jarvisvoice.protocol;

import java.io.DataInputStream;
import java.io.DataOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.nio.charset.StandardCharsets;

public final class WearWireProtocol {
    public static final String CHANNEL_PATH = "/jarvis/watch/voice/v1";
    public static final int SAMPLE_RATE = 24_000;
    public static final byte START = 1, MIC_AUDIO = 2, END_INPUT = 3, CANCEL = 4;
    public static final byte STATE = 11, OUTPUT_AUDIO = 12, OUTPUT_DONE = 13, CLOSED = 14, ERROR = 15, OUTPUT_CANCEL = 16;
    public static final int MAX_FRAME_BYTES = 256 * 1024;

    public record Frame(byte type, long generation, byte[] payload) {}

    private WearWireProtocol() {}

    public static synchronized void write(OutputStream stream, byte type, long generation, byte[] payload) throws IOException {
        byte[] body = payload == null ? new byte[0] : payload;
        if (body.length > MAX_FRAME_BYTES) throw new IOException("frame too large");
        DataOutputStream output = new DataOutputStream(stream);
        output.writeByte(type);
        output.writeLong(generation);
        output.writeInt(body.length);
        output.write(body);
        output.flush();
    }

    public static void writeText(OutputStream stream, byte type, long generation, String text) throws IOException {
        write(stream, type, generation, text.getBytes(StandardCharsets.UTF_8));
    }

    public static Frame read(InputStream stream) throws IOException {
        DataInputStream input = new DataInputStream(stream);
        byte type = input.readByte();
        long generation = input.readLong();
        int size = input.readInt();
        if (size < 0 || size > MAX_FRAME_BYTES) throw new IOException("invalid frame size " + size);
        byte[] payload = new byte[size];
        input.readFully(payload);
        return new Frame(type, generation, payload);
    }

    public static String text(Frame frame) { return new String(frame.payload(), StandardCharsets.UTF_8); }
}
