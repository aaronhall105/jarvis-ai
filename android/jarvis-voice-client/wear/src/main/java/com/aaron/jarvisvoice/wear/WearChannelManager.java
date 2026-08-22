package com.aaron.jarvisvoice.wear;

import android.content.Context;
import android.os.SystemClock;
import android.util.Log;
import com.aaron.jarvisvoice.protocol.WearWireProtocol;
import com.aaron.jarvisvoice.protocol.StartupAudioBuffer;
import com.google.android.gms.tasks.Tasks;
import com.google.android.gms.wearable.ChannelClient;
import com.google.android.gms.wearable.Node;
import com.google.android.gms.wearable.Wearable;
import java.io.InputStream;
import java.io.OutputStream;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicLong;

final class WearChannelManager {
    private static final String TAG = "JarvisWearChannel";
    private static final int STARTUP_AUDIO_BYTES = WearWireProtocol.SAMPLE_RATE * 2 * 2;
    private static volatile Node cachedNearbyNode;
    interface Listener { void onFrame(WearWireProtocol.Frame frame); void onDisconnected(String reason); }
    private final ChannelClient client; private final Listener listener;
    private final ExecutorService reader = Executors.newSingleThreadExecutor();
    private final ExecutorService writer = Executors.newSingleThreadExecutor();
    private final AtomicLong connectionGeneration = new AtomicLong();
    private volatile ChannelClient.Channel channel; private volatile OutputStream output; private volatile boolean closed = true;
    private volatile boolean acceptingSends;
    private volatile boolean protocolReady;
    private final StartupAudioBuffer startupAudio = new StartupAudioBuffer(STARTUP_AUDIO_BYTES);
    private volatile long connectStartedAtMs;
    private volatile boolean firstMicLogged;
    private volatile boolean firstAudioLogged;

    WearChannelManager(Context context, Listener listener) { client = Wearable.getChannelClient(context); this.listener = listener; }
    void connect(Context context, long generation) {
        close(); closed = false; acceptingSends = true; protocolReady = false;
        startupAudio.begin(generation);
        connectStartedAtMs = SystemClock.elapsedRealtime(); firstMicLogged = false; firstAudioLogged = false;
        Log.i(TAG, "WATCH_LATENCY connect_start generation=" + generation + " elapsed_ms=" + connectStartedAtMs);
        long acceptedConnection = connectionGeneration.incrementAndGet();
        reader.execute(() -> {
            try {
                Node target = cachedNearbyNode;
                if (target != null) {
                    Log.i(TAG, "WATCH_LATENCY cached_node_fast_path node=" + target.getId());
                }
                if (target == null) target = discoverNearbyNode(context);
                if (target == null) throw new IllegalStateException("Phone not connected");
                ChannelClient.Channel opened;
                try {
                    opened = Tasks.await(
                        client.openChannel(target.getId(), WearWireProtocol.CHANNEL_PATH),
                        cachedNearbyNode == target ? 2L : 10L,
                        TimeUnit.SECONDS
                    );
                } catch (Exception cachedFailure) {
                    if (cachedNearbyNode != target) throw cachedFailure;
                    cachedNearbyNode = null;
                    target = discoverNearbyNode(context);
                    if (target == null) throw new IllegalStateException("Phone not connected");
                    opened = Tasks.await(
                        client.openChannel(target.getId(), WearWireProtocol.CHANNEL_PATH),
                        10L,
                        TimeUnit.SECONDS
                    );
                }
                cachedNearbyNode = target;
                if (closed || acceptedConnection != connectionGeneration.get()) {
                    client.close(opened);
                    return;
                }
                channel = opened;
                output = Tasks.await(client.getOutputStream(opened), 10L, TimeUnit.SECONDS);
                InputStream input = Tasks.await(client.getInputStream(opened), 10L, TimeUnit.SECONDS);
                WearWireProtocol.write(output, WearWireProtocol.START, generation, new byte[0]);
                protocolReady = true;
                flushStartupAudio(generation, acceptedConnection, opened);
                Log.i(TAG, "WATCH_LATENCY channel_ready generation=" + generation + " duration_ms=" + (SystemClock.elapsedRealtime() - connectStartedAtMs));
                while (!closed && acceptedConnection == connectionGeneration.get()) {
                    WearWireProtocol.Frame frame = WearWireProtocol.read(input);
                    if (frame.type() == WearWireProtocol.OUTPUT_AUDIO && !firstAudioLogged) {
                        firstAudioLogged = true;
                        Log.i(TAG, "WATCH_LATENCY first_audio_received generation=" + frame.generation() + " bytes=" + frame.payload().length + " since_connect_ms=" + (SystemClock.elapsedRealtime() - connectStartedAtMs));
                    }
                    listener.onFrame(frame);
                }
            } catch (Exception error) {
                if (!closed && acceptedConnection == connectionGeneration.get()) {
                    listener.onDisconnected(error.getMessage() == null ? "Phone link lost" : error.getMessage());
                }
            }
        });
    }
    void send(byte type, long generation, byte[] payload) {
        OutputStream current = output;
        ChannelClient.Channel currentChannel = channel;
        long acceptedConnection = connectionGeneration.get();
        if (closed || !acceptingSends) return;
        if (!protocolReady || current == null || currentChannel == null) {
            if (type == WearWireProtocol.MIC_AUDIO) startupAudio.offer(generation, payload);
            return;
        }
        writer.execute(() -> {
            try {
                if (!closed && acceptingSends && currentChannel == channel
                        && acceptedConnection == connectionGeneration.get()) {
                    WearWireProtocol.write(current, type, generation, payload);
                    if (type == WearWireProtocol.MIC_AUDIO && !firstMicLogged) {
                        firstMicLogged = true;
                        Log.i(TAG, "WATCH_LATENCY first_mic_sent generation=" + generation + " bytes=" + payload.length + " since_connect_ms=" + (SystemClock.elapsedRealtime() - connectStartedAtMs));
                    }
                }
            } catch (Exception error) {
                if (!closed && acceptedConnection == connectionGeneration.get()) {
                    listener.onDisconnected("Phone link lost");
                }
            }
        });
    }
    void sendText(byte type, long generation, String value) {
        send(type, generation, value == null ? new byte[0] : value.getBytes(java.nio.charset.StandardCharsets.UTF_8));
    }
    boolean isReady() { return !closed && output != null && channel != null; }
    void cancelAndClose(long generation, Runnable completion) {
        OutputStream current = output;
        ChannelClient.Channel currentChannel = channel;
        long acceptedConnection = connectionGeneration.get();
        acceptingSends = false;
        if (closed || current == null || currentChannel == null) {
            close();
            completion.run();
            return;
        }
        writer.execute(() -> {
            try {
                if (!closed && currentChannel == channel
                        && acceptedConnection == connectionGeneration.get()) {
                    WearWireProtocol.write(
                        current,
                        WearWireProtocol.CANCEL,
                        generation,
                        new byte[0]
                    );
                }
            } catch (Exception ignored) {
            } finally {
                close();
                completion.run();
            }
        });
    }
    synchronized void close() {
        connectionGeneration.incrementAndGet();
        closed = true; acceptingSends = false; protocolReady = false; startupAudio.reset(); OutputStream old = output; output = null; ChannelClient.Channel oldChannel = channel; channel = null;
        if (old != null) try { old.close(); } catch (Exception ignored) {}
        if (oldChannel != null) client.close(oldChannel);
    }
    void shutdown() {
        close();
        reader.shutdownNow();
        writer.shutdownNow();
    }

    private Node discoverNearbyNode(Context context) throws Exception {
        for (Node node : Tasks.await(
                Wearable.getNodeClient(context).getConnectedNodes(),
                10L,
                TimeUnit.SECONDS)) {
            if (node.isNearby()) return node;
        }
        return null;
    }

    private void flushStartupAudio(long generation, long acceptedConnection, ChannelClient.Channel opened) {
        java.util.List<byte[]> frames = startupAudio.drain(generation);
        if (frames.isEmpty()) return;
        Log.i(TAG, "WATCH_LATENCY startup_audio_flush generation=" + generation
            + " frames=" + frames.size());
        writer.execute(() -> {
            try {
                OutputStream current = output;
                if (closed || !protocolReady || opened != channel || current == null
                        || acceptedConnection != connectionGeneration.get()) return;
                for (byte[] frame : frames) {
                    WearWireProtocol.write(current, WearWireProtocol.MIC_AUDIO, generation, frame);
                    if (!firstMicLogged) {
                        firstMicLogged = true;
                        Log.i(TAG, "WATCH_LATENCY first_mic_sent generation=" + generation
                            + " bytes=" + frame.length + " since_connect_ms="
                            + (SystemClock.elapsedRealtime() - connectStartedAtMs) + " buffered=true");
                    }
                }
            } catch (Exception error) {
                if (!closed && acceptedConnection == connectionGeneration.get()) {
                    listener.onDisconnected("Phone link lost");
                }
            }
        });
    }
}
