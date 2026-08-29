package com.aaron.jarvisvoice.protocol;

/** Holds the first typed turn until the phone confirms Core session readiness. */
public final class ColdStartTextGate {
    private long generation;
    private boolean ready;
    private String pending = "";
    public synchronized void begin(long value) { generation = value; ready = false; pending = ""; }
    public synchronized boolean offer(long value, String text) {
        if (value != generation) return false;
        if (ready) return true;
        pending = text == null ? "" : text;
        return false;
    }
    public synchronized String markReady(long value) {
        if (value != generation) return "";
        ready = true; String valuePending = pending; pending = ""; return valuePending;
    }
    public synchronized boolean isReady(long value) { return value == generation && ready; }
    public synchronized void reset() { generation = 0L; ready = false; pending = ""; }
}
