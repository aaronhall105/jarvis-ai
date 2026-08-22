package com.aaron.jarvisvoice.protocol;

public final class AudioEndpointRouter {
    public interface Sink { void enqueue(byte[] pcm, long generation); void interrupt(); }
    private final Sink phone; private final Sink watch;
    private VoiceEndpoint endpoint = VoiceEndpoint.PHONE; private long generation; private boolean active;
    public AudioEndpointRouter(Sink phone, Sink watch) { this.phone = phone; this.watch = watch; }
    public synchronized void begin(VoiceEndpoint value, long valueGeneration) { interrupt(); endpoint = value; generation = valueGeneration; active = true; }
    public synchronized void enqueue(byte[] pcm, long frameGeneration) { if (!active || frameGeneration != generation) return; (endpoint == VoiceEndpoint.WATCH ? watch : phone).enqueue(pcm, generation); }
    public synchronized void interrupt() { active = false; phone.interrupt(); watch.interrupt(); }
    public synchronized VoiceEndpoint endpoint() { return endpoint; }
    public synchronized long generation() { return generation; }
    public synchronized boolean active() { return active; }
}
