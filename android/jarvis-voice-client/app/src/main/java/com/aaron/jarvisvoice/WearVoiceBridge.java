package com.aaron.jarvisvoice;

import android.content.Context;
import android.os.SystemClock;
import android.util.Log;
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
    private static final String TAG = "JarvisWearBridge";
    private static volatile WearVoiceBridge active;
    private static volatile ChannelClient.Channel pending;
    interface Listener { void onWatchPrepare(); void onWatchStart(long generation); void onWatchAudio(long generation, byte[] pcm); void onWatchText(long generation, String text); void onWatchClearChat(long generation); void onWatchCancel(long generation); void onWatchSilenceTimeout(long generation); void onWatchDisconnected(); }
    private final ChannelClient client; private final Listener listener; private final ExecutorService reader = Executors.newSingleThreadExecutor(); private final ExecutorService writer = Executors.newSingleThreadExecutor();
    private volatile ChannelClient.Channel channel; private volatile OutputStream output; private volatile long generation;
    private final AtomicLong outputEpoch = new AtomicLong();
    private volatile long openedAtMs;
    private volatile boolean firstMicLogged;
    private volatile boolean firstOutputLogged;
    WearVoiceBridge(Context context, Listener listener) { client = Wearable.getChannelClient(context); this.listener = listener; active = this; client.registerChannelCallback(this); ChannelClient.Channel waiting = pending; pending = null; if (waiting != null) onChannelOpened(waiting); }
    static void acceptFromSystem(ChannelClient.Channel opened) { WearVoiceBridge bridge = active; if (bridge == null) pending = opened; else bridge.onChannelOpened(opened); }
    @Override public void onChannelOpened(ChannelClient.Channel opened) {
        if (!WearWireProtocol.CHANNEL_PATH.equals(opened.getPath())) { client.close(opened); return; }
        if (opened.equals(channel)) return;
        closeChannel(); channel = opened; openedAtMs = SystemClock.elapsedRealtime();
        firstMicLogged = false; firstOutputLogged = false;
        Log.i(TAG, "channel_opened path=" + opened.getPath());
        reader.execute(() -> {
            try {
                output = Tasks.await(client.getOutputStream(opened)); InputStream input = Tasks.await(client.getInputStream(opened));
                while (channel == opened) {
                    WearWireProtocol.Frame frame = WearWireProtocol.read(input);
                    if (frame.type() == WearWireProtocol.PREPARE) listener.onWatchPrepare();
                    else if (frame.type() == WearWireProtocol.START) { outputEpoch.incrementAndGet(); generation = frame.generation(); Log.i(TAG, "session_start generation=" + generation + " channel_ready_ms=" + (SystemClock.elapsedRealtime() - openedAtMs)); listener.onWatchStart(generation); }
                    else if (frame.generation() == generation && frame.type() == WearWireProtocol.MIC_AUDIO) { if (!firstMicLogged) { firstMicLogged = true; Log.i(TAG, "first_mic_frame generation=" + generation + " bytes=" + frame.payload().length + " since_open_ms=" + (SystemClock.elapsedRealtime() - openedAtMs)); } listener.onWatchAudio(generation, frame.payload()); }
                    else if (frame.generation() == generation && frame.type() == WearWireProtocol.TEXT_INPUT) listener.onWatchText(generation, WearWireProtocol.text(frame));
                    else if (frame.generation() == generation && frame.type() == WearWireProtocol.CLEAR_CHAT) listener.onWatchClearChat(generation);
                    else if (frame.generation() == generation && frame.type() == WearWireProtocol.CANCEL) listener.onWatchCancel(generation);
                    else if (frame.generation() == generation && frame.type() == WearWireProtocol.SILENCE_TIMEOUT) listener.onWatchSilenceTimeout(generation);
                }
            } catch (Exception error) { if (channel == opened) { Log.e(TAG, "channel_read_failed generation=" + generation, error); listener.onWatchDisconnected(); } }
        });
    }
    @Override public void onChannelClosed(ChannelClient.Channel closed, int closeReason, int appErrorCode) { if (closed.equals(channel)) { closeChannel(); listener.onWatchDisconnected(); } }
    void state(String state, long frameGeneration) { sendText(WearWireProtocol.STATE, state, frameGeneration); }
    void audio(byte[] pcm, long frameGeneration) { send(WearWireProtocol.OUTPUT_AUDIO, pcm, frameGeneration); }
    void audioDone(long frameGeneration) { send(WearWireProtocol.OUTPUT_DONE, new byte[0], frameGeneration); }
    void error(String message, long frameGeneration) { sendText(WearWireProtocol.ERROR, message, frameGeneration); }
    void userTranscript(String text, long frameGeneration) { sendText(WearWireProtocol.USER_TRANSCRIPT, text, frameGeneration); }
    void assistantDelta(String text, long frameGeneration) { sendText(WearWireProtocol.ASSISTANT_DELTA, text, frameGeneration); }
    void assistantDone(String text, long frameGeneration) { sendText(WearWireProtocol.ASSISTANT_DONE, text, frameGeneration); }
    void transcriptCleared(long frameGeneration) { send(WearWireProtocol.TRANSCRIPT_CLEARED, new byte[0], frameGeneration); }
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
                    if (type == WearWireProtocol.OUTPUT_AUDIO && !firstOutputLogged) {
                        firstOutputLogged = true;
                        Log.i(TAG, "first_output_frame_written generation=" + frameGeneration + " bytes=" + bytes.length + " since_open_ms=" + (SystemClock.elapsedRealtime() - openedAtMs));
                    }
                }
            } catch (Exception error) { Log.e(TAG, "channel_write_failed type=" + type + " generation=" + frameGeneration, error); }
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
