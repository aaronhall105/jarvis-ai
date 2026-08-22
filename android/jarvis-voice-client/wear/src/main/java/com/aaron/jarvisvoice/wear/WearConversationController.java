package com.aaron.jarvisvoice.wear;

import android.content.Context;
import android.os.Handler;
import android.os.Looper;
import com.aaron.jarvisvoice.protocol.WatchConversationMachine;
import com.aaron.jarvisvoice.protocol.WatchConversationState;
import com.aaron.jarvisvoice.protocol.WatchMicrophonePolicy;
import com.aaron.jarvisvoice.protocol.WearWireProtocol;
import com.aaron.jarvisvoice.protocol.SpeechSilencePolicy;
import com.aaron.jarvisvoice.protocol.ColdStartTextGate;

final class WearConversationController implements WearChannelManager.Listener {
    interface Listener { void onState(WatchConversationState state, String message); void onTranscript(String role, String text, boolean complete); void onEnded(); }
    private final Context context; private final Listener listener; private final Handler main = new Handler(Looper.getMainLooper());
    private final WatchConversationMachine machine; private final WearAudioRecorder recorder;
    private final WearAudioPlayer player; private final WearChannelManager channel;
    private final SpeechSilencePolicy silence = new SpeechSilencePolicy();
    private final Runnable timeout = this::silenceTimeout;
    private final ColdStartTextGate textGate = new ColdStartTextGate();
    private boolean pendingClear;

    WearConversationController(Context context, Listener listener, long timeoutMs) {
        this.context = context.getApplicationContext(); this.listener = listener; machine = new WatchConversationMachine(timeoutMs);
        recorder = new WearAudioRecorder(context); player = new WearAudioPlayer(context); channel = new WearChannelManager(context, this);
    }
    synchronized void start() {
        start(true);
    }
    synchronized void prepareTransport() {
        if (machine.state() == WatchConversationState.IDLE) channel.prepare(context);
    }
    private synchronized void start(boolean captureImmediately) {
        if (machine.state() != WatchConversationState.IDLE) return;
        long generation = machine.start();
        player.begin(generation);
        textGate.begin(generation);
        publish("Connecting…");
        channel.connect(context, generation);
        if (captureImmediately) startMic();
    }
    synchronized void cancel() { pause("Ready", WearWireProtocol.CANCEL); }
    synchronized void sendText(String rawText) {
        String text = rawText == null ? "" : rawText.trim();
        if (text.isEmpty()) return;
        if (machine.state() == WatchConversationState.IDLE) start(false);
        recorder.stop(); cancelTimeout();
        listener.onTranscript("user", text, true);
        if (textGate.offer(machine.generation(), text)) {
            machine.processing(machine.generation()); publish("Processing");
            channel.sendText(WearWireProtocol.TEXT_INPUT, machine.generation(), text);
        } else {
            publish("Connecting…");
        }
    }
    synchronized void clearChat() {
        if (machine.state() == WatchConversationState.IDLE) start(false);
        if (textGate.isReady(machine.generation())) channel.send(WearWireProtocol.CLEAR_CHAT, machine.generation(), new byte[0]);
        else pendingClear = true;
    }
    boolean microphoneActive() { return recorder.isActive(); }
    WatchConversationState state() { return machine.state(); }
    long generation() { return machine.generation(); }
    @Override public void onFrame(WearWireProtocol.Frame frame) {
        main.post(() -> handle(frame));
    }
    private synchronized void handle(WearWireProtocol.Frame frame) {
        if (!machine.accepts(frame.generation())) return;
        switch (frame.type()) {
            case WearWireProtocol.STATE -> {
                String state = WearWireProtocol.text(frame);
                if ("LISTENING".equals(state)
                        && machine.state() != WatchConversationState.SPEAKING) {
                    String pendingText = textGate.markReady(frame.generation());
                    if (pendingClear) {
                        pendingClear = false;
                        channel.send(WearWireProtocol.CLEAR_CHAT, frame.generation(), new byte[0]);
                    } else if (!pendingText.isEmpty()) {
                        String text = pendingText;
                        recorder.stop(); machine.processing(frame.generation());
                        channel.sendText(WearWireProtocol.TEXT_INPUT, frame.generation(), text);
                    } else {
                        startMic();
                    }
                }
                else if ("PROCESSING".equals(state)) { recorder.stop(); machine.processing(frame.generation()); cancelTimeout(); }
                else if ("SPEAKING".equals(state)) { recorder.stop(); machine.speaking(frame.generation()); cancelTimeout(); }
                publish(stateLabel());
            }
            case WearWireProtocol.OUTPUT_AUDIO -> { recorder.stop(); machine.speaking(frame.generation()); publish("Speaking…"); player.play(frame.payload(), frame.generation()); }
            case WearWireProtocol.OUTPUT_DONE -> player.finish(
                frame.generation(),
                () -> playbackComplete(frame.generation())
            );
            case WearWireProtocol.OUTPUT_CANCEL -> player.cancelTurn(frame.generation());
            case WearWireProtocol.USER_TRANSCRIPT -> listener.onTranscript("user", WearWireProtocol.text(frame), true);
            case WearWireProtocol.ASSISTANT_DELTA -> listener.onTranscript("assistant", WearWireProtocol.text(frame), false);
            case WearWireProtocol.ASSISTANT_DONE -> listener.onTranscript("assistant", WearWireProtocol.text(frame), true);
            case WearWireProtocol.TRANSCRIPT_CLEARED -> listener.onTranscript("clear", "", true);
            case WearWireProtocol.CLOSED -> end("Ready", false);
            case WearWireProtocol.ERROR -> end(WearWireProtocol.text(frame), false);
            default -> { }
        }
    }
    private synchronized void playbackComplete(long generation) {
        if (!machine.playbackComplete(generation)) return;
        startMic();
        publish("Listening…");
    }
    private synchronized void startMic() {
        if (!WatchMicrophonePolicy.shouldCapture(machine.state())) return;
        if (recorder.isActive()) return;
        silence.reset();
        armTimeout();
        long accepted = machine.generation();
        recorder.start(new WearAudioRecorder.Listener() {
            public void onAudio(byte[] pcm) {
                if (!machine.accepts(accepted)) return;
                if (silence.acceptPcm16(pcm)) cancelTimeout();
                channel.send(WearWireProtocol.MIC_AUDIO, accepted, pcm);
            }
            public void onFailure(String message) { main.post(() -> end(message, true)); }
        });
    }
    private synchronized void silenceTimeout() {
        if (machine.state() != WatchConversationState.LISTENING || silence.speechStarted()) return;
        long generation = machine.generation();
        recorder.stop(); player.interrupt(); machine.inactivityTimeout();
        channel.pauseSession(WearWireProtocol.SILENCE_TIMEOUT, generation);
        listener.onState(WatchConversationState.IDLE, "Ready");
    }
    private synchronized void pause(String message, byte reason) {
        if (machine.state() == WatchConversationState.IDLE) return;
        long generation = machine.generation();
        recorder.stop(); player.interrupt(); cancelTimeout();
        machine.end(); textGate.reset(); pendingClear = false; channel.pauseSession(reason, generation);
        listener.onState(WatchConversationState.IDLE, message);
    }
    private synchronized void end(String message, boolean notifyPhone) {
        if (machine.state() == WatchConversationState.IDLE) return;
        long oldGeneration = machine.generation(); recorder.stop(); player.interrupt(); cancelTimeout();
        machine.end();
        textGate.reset(); pendingClear = false;
        listener.onState(WatchConversationState.IDLE, message);
        if (notifyPhone) channel.cancelAndClose(oldGeneration, listener::onEnded);
        else { channel.close(); listener.onEnded(); }
    }
    @Override public void onDisconnected(String reason) { main.post(() -> end(reason, false)); }
    void close() { end("Ready", false); player.close(); channel.shutdown(); }
    private void armTimeout() { cancelTimeout(); main.postDelayed(timeout, machine.inactivityTimeoutMs()); }
    private void cancelTimeout() { main.removeCallbacks(timeout); }
    private void publish(String message) { listener.onState(machine.state(), message); }
    private String stateLabel() { return switch (machine.state()) { case LISTENING, FOLLOW_UP -> "Listening…"; case PROCESSING -> "Thinking…"; case SPEAKING -> "Speaking…"; default -> "Ready"; }; }
}
