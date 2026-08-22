package com.aaron.jarvisvoice.wear;

import android.content.Context;
import android.os.Handler;
import android.os.Looper;
import com.aaron.jarvisvoice.protocol.WatchConversationMachine;
import com.aaron.jarvisvoice.protocol.WatchConversationState;
import com.aaron.jarvisvoice.protocol.WatchMicrophonePolicy;
import com.aaron.jarvisvoice.protocol.WearWireProtocol;

final class WearConversationController implements WearChannelManager.Listener {
    interface Listener { void onState(WatchConversationState state, String message); void onEnded(); }
    private final Context context; private final Listener listener; private final Handler main = new Handler(Looper.getMainLooper());
    private final WatchConversationMachine machine; private final WearAudioRecorder recorder;
    private final WearAudioPlayer player = new WearAudioPlayer(); private final WearChannelManager channel;
    private final Runnable timeout = () -> end("Conversation timed out", true);

    WearConversationController(Context context, Listener listener, long timeoutMs) {
        this.context = context.getApplicationContext(); this.listener = listener; machine = new WatchConversationMachine(timeoutMs);
        recorder = new WearAudioRecorder(context); channel = new WearChannelManager(context, this);
    }
    synchronized void start() {
        if (machine.state() != WatchConversationState.IDLE) return;
        long generation = machine.start(); player.begin(generation); publish("Connecting…"); channel.connect(context, generation);
    }
    synchronized void cancel() { end("Ready", true); }
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
                    startMic();
                    armTimeout();
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
            case WearWireProtocol.OUTPUT_CANCEL -> player.interrupt();
            case WearWireProtocol.CLOSED -> end("Ready", false);
            case WearWireProtocol.ERROR -> end(WearWireProtocol.text(frame), false);
            default -> { }
        }
    }
    private synchronized void playbackComplete(long generation) {
        if (!machine.playbackComplete(generation)) return;
        startMic();
        armTimeout();
        publish("Listening…");
    }
    private void startMic() {
        if (!WatchMicrophonePolicy.shouldCapture(machine.state())) return;
        long accepted = machine.generation();
        recorder.start(new WearAudioRecorder.Listener() {
            public void onAudio(byte[] pcm) { if (machine.accepts(accepted)) channel.send(WearWireProtocol.MIC_AUDIO, accepted, pcm); }
            public void onFailure(String message) { main.post(() -> end(message, true)); }
        });
    }
    private synchronized void end(String message, boolean notifyPhone) {
        if (machine.state() == WatchConversationState.IDLE) return;
        long oldGeneration = machine.generation(); recorder.stop(); player.interrupt(); cancelTimeout();
        machine.end();
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
