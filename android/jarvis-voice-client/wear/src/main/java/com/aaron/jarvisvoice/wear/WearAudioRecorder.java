package com.aaron.jarvisvoice.wear;

import android.Manifest;
import android.annotation.SuppressLint;
import android.content.Context;
import android.content.pm.PackageManager;
import android.media.AudioFormat;
import android.media.AudioRecord;
import android.media.MediaRecorder;
import com.aaron.jarvisvoice.protocol.WearWireProtocol;

final class WearAudioRecorder {
    interface Listener { void onAudio(byte[] pcm); void onFailure(String message); }
    private volatile boolean running;
    private volatile AudioRecord record;
    private final Context context;

    WearAudioRecorder(Context context) {
        this.context = context.getApplicationContext();
    }

    synchronized void start(Listener listener) {
        if (running) return;
        if (context.checkSelfPermission(Manifest.permission.RECORD_AUDIO)
                != PackageManager.PERMISSION_GRANTED) {
            listener.onFailure("Microphone permission is required");
            return;
        }
        running = true;
        new Thread(() -> capture(listener), "jarvis-watch-mic").start();
    }
    synchronized void stop() {
        running = false;
        AudioRecord current = record;
        if (current != null) try { current.stop(); } catch (Exception ignored) {}
    }
    boolean isActive() { return running; }
    @SuppressLint("MissingPermission")
    private void capture(Listener listener) {
        AudioRecord local = null;
        try {
            int minimum = AudioRecord.getMinBufferSize(WearWireProtocol.SAMPLE_RATE, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT);
            local = new AudioRecord(
                MediaRecorder.AudioSource.VOICE_COMMUNICATION,
                WearWireProtocol.SAMPLE_RATE,
                AudioFormat.CHANNEL_IN_MONO,
                AudioFormat.ENCODING_PCM_16BIT,
                Math.max(minimum * 2, 3840)
            );
            if (local.getState() != AudioRecord.STATE_INITIALIZED) throw new IllegalStateException("Watch microphone unavailable");
            record = local; local.startRecording(); byte[] frame = new byte[960];
            while (running) { int count = local.read(frame, 0, frame.length, AudioRecord.READ_BLOCKING); if (count > 0) { byte[] copy = new byte[count]; System.arraycopy(frame, 0, copy, 0, count); listener.onAudio(copy); } }
        } catch (Exception error) { if (running) listener.onFailure(error.getMessage() == null ? "Microphone failed" : error.getMessage()); }
        finally { running = false; record = null; if (local != null) { try { local.stop(); } catch (Exception ignored) {} local.release(); } }
    }
}
