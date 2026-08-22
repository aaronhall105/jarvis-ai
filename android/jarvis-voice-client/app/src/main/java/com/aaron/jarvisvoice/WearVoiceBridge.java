package com.aaron.jarvisvoice;

import android.content.Context;
import com.aaron.jarvisvoice.protocol.WearWireProtocol;
import com.google.android.gms.tasks.Tasks;
import com.google.android.gms.wearable.ChannelClient;
import com.google.android.gms.wearable.Wearable;
import java.io.InputStream;
import java.io.OutputStream;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.atomic.AtomicLong;

final class WearVoiceBridge extends ChannelClient.ChannelCallback {
    private static volatile WearVoiceBridge active;
    private static volatile ChannelClient.Channel pending;
    interface Listener { void onWatchStart(long generation); void onWatchAudio(long generation, byte[] pcm); void onWatchCancel(long generation); void onWatchDisconnected(); }
    private final ChannelClient client; private final Listener listener; private final ExecutorService reader = Executors.newSingleThreadExecutor(); private final ExecutorService writer = Executors.newSingleThreadExecutor();
    private volatile ChannelClient.Channel channel; private volatile OutputStream output; private volatile long generation;
    private final AtomicLong outputEpoch = new AtomicLong();
    WearVoiceBridge(Context context, Listener listener) { client = Wearable.getChannelClient(context); this.listener = listener; active = this; client.registerChannelCallback(this); ChannelClient.Channel waiting = pending; pending = null; if (waiting != null) onChannelOpened(waiting); }
    static void acceptFromSystem(ChannelClient.Channel opened) { WearVoiceBridge bridge = active; if (bridge == null) pending = opened; else bridge.onChannelOpened(opened); }
    @Override public void onChannelOpened(ChannelClient.Channel opened) {
        if (!WearWireProtocol.CHANNEL_PATH.equals(opened.getPath())) { client.close(opened); return; }
        if (opened.equals(channel)) return;
        closeChannel(); channel = opened;
        reader.execute(() -> {
            try {
                output = Tasks.await(client.getOutputStream(opened)); InputStream input = Tasks.await(client.getInputStream(opened));
                while (channel == opened) {
                    WearWireProtocol.Frame frame = WearWireProtocol.read(input);
                    if (frame.type() == WearWireProtocol.START) { outputEpoch.incrementAndGet(); generation = frame.generation(); listener.onWatchStart(generation); }
                    else if (frame.generation() == generation && frame.type() == WearWireProtocol.MIC_AUDIO) listener.onWatchAudio(generation, frame.payload());
                    else if (frame.generation() == generation && frame.type() == WearWireProtocol.CANCEL) listener.onWatchCancel(generation);
                }
            } catch (Exception error) { if (channel == opened) listener.onWatchDisconnected(); }
        });
    }
    @Override public void onChannelClosed(ChannelClient.Channel closed, int closeReason, int appErrorCode) { if (closed.equals(channel)) { closeChannel(); listener.onWatchDisconnected(); } }
    void state(String state, long frameGeneration) { sendText(WearWireProtocol.STATE, state, frameGeneration); }
    void audio(byte[] pcm, long frameGeneration) { send(WearWireProtocol.OUTPUT_AUDIO, pcm, frameGeneration); }
    void audioDone(long frameGeneration) { send(WearWireProtocol.OUTPUT_DONE, new byte[0], frameGeneration); }
    void error(String message, long frameGeneration) { sendText(WearWireProtocol.ERROR, message, frameGeneration); }
    void sessionClosed(long frameGeneration) {
        ChannelClient.Channel target = channel;
        OutputStream targetOutput = output;
        if (target == null) return;
        writer.execute(() -> {
            try {
                if (target != null && target == channel && targetOutput != null
                        && frameGeneration == generation) {
                    WearWireProtocol.write(
                        targetOutput,
                        WearWireProtocol.CLOSED,
                        frameGeneration,
                        new byte[0]
                    );
                }
            } catch (Exception ignored) {
            } finally {
                closeChannel(target);
            }
        });
    }
    void interrupt() { outputEpoch.incrementAndGet(); send(WearWireProtocol.OUTPUT_CANCEL, new byte[0], generation); }
    private void sendText(byte type, String text, long frameGeneration) { writer.execute(() -> { try { OutputStream current = output; if (current != null && frameGeneration == generation) WearWireProtocol.writeText(current, type, frameGeneration, text); } catch (Exception ignored) {} }); }
    private void send(byte type, byte[] bytes, long frameGeneration) {
        ChannelClient.Channel target = channel;
        OutputStream targetOutput = output;
        long acceptedEpoch = outputEpoch.get();
        writer.execute(() -> {
            try {
                if (target != null && target == channel && targetOutput != null
                        && frameGeneration == generation
                        && acceptedEpoch == outputEpoch.get()) {
                    WearWireProtocol.write(targetOutput, type, frameGeneration, bytes);
                }
            } catch (Exception ignored) {}
        });
    }
    void close() { if (active == this) active = null; client.unregisterChannelCallback(this); closeChannel(); reader.shutdownNow(); writer.shutdownNow(); }
    private void closeChannel() { closeChannel(channel); }
    private synchronized void closeChannel(ChannelClient.Channel expected) {
        if (expected != null && expected != channel) return;
        ChannelClient.Channel old = channel;
        channel = null;
        output = null;
        generation = 0L;
        outputEpoch.incrementAndGet();
        if (old != null) client.close(old);
    }
}
