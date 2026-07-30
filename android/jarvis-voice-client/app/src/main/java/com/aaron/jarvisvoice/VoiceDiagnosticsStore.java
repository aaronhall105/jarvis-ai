package com.aaron.jarvisvoice;

import android.content.Context;
import android.content.SharedPreferences;

import java.text.DateFormat;
import java.util.Date;

public final class VoiceDiagnosticsStore {
    private static final String PREFS =
        "jarvis_voice_foundation_diagnostics";

    private final SharedPreferences preferences;

    public VoiceDiagnosticsStore(Context context) {
        preferences = context.getApplicationContext()
            .getSharedPreferences(PREFS, Context.MODE_PRIVATE);
    }

    public void recordState(String state, String owner, String reason) {
        preferences.edit()
            .putString("state", safe(state))
            .putString("owner", safe(owner))
            .putString("reason", safe(reason))
            .putLong("updated_at", System.currentTimeMillis())
            .apply();
    }

    public void recordAudioProcessing(String summary) {
        preferences.edit()
            .putString("audio_processing", safe(summary))
            .apply();
    }

    public void recordNetwork(String summary) {
        preferences.edit()
            .putString("network", safe(summary))
            .putLong("network_updated_at", System.currentTimeMillis())
            .apply();
    }

    public void recordConnectionLatency(long milliseconds) {
        preferences.edit()
            .putLong("connect_latency_ms", Math.max(0L, milliseconds))
            .apply();
    }

    public void recordRoundTrip(long milliseconds) {
        preferences.edit()
            .putLong("round_trip_ms", Math.max(0L, milliseconds))
            .apply();
    }

    public void recordFirstAudioLatency(long milliseconds) {
        preferences.edit()
            .putLong("first_audio_ms", Math.max(0L, milliseconds))
            .apply();
    }

    public void recordReconnect(int attempt, long delay, String reason) {
        preferences.edit()
            .putInt("last_reconnect_attempt", Math.max(0, attempt))
            .putLong("last_reconnect_delay_ms", Math.max(0L, delay))
            .putString("last_reconnect_reason", safe(reason))
            .apply();
    }

    public void recordRecovery(String reason) {
        int count = preferences.getInt("recovery_count", 0);
        preferences.edit()
            .putInt("recovery_count", count + 1)
            .putString("last_recovery", safe(reason))
            .apply();
    }

    public void recordInvalidTransition(String from, String to, String reason) {
        int count = preferences.getInt("invalid_transition_count", 0);
        preferences.edit()
            .putInt("invalid_transition_count", count + 1)
            .putString(
                "last_invalid_transition",
                safe(from) + " → " + safe(to) + " · " + safe(reason)
            )
            .apply();
    }

    public String summary() {
        String state = preferences.getString("state", "Not started");
        String owner = preferences.getString("owner", "NONE");
        String audio = preferences.getString(
            "audio_processing",
            "Audio processing not measured yet"
        );
        String network = preferences.getString("network", "Not measured");
        long connect = preferences.getLong("connect_latency_ms", -1L);
        long roundTrip = preferences.getLong("round_trip_ms", -1L);
        long firstAudio = preferences.getLong("first_audio_ms", -1L);
        int reconnectAttempt = preferences.getInt("last_reconnect_attempt", 0);
        long reconnectDelay = preferences.getLong("last_reconnect_delay_ms", 0L);
        int recoveries = preferences.getInt("recovery_count", 0);
        int invalid = preferences.getInt("invalid_transition_count", 0);
        long updated = preferences.getLong("updated_at", 0L);

        String time = updated <= 0L
            ? "never"
            : DateFormat.getDateTimeInstance(
                DateFormat.SHORT,
                DateFormat.SHORT
            ).format(new Date(updated));

        return "State: " + state
            + "\nMicrophone: " + owner
            + "\nAudio: " + audio
            + "\nNetwork: " + network
            + "\nConnect: " + latency(connect)
            + " · RTT: " + latency(roundTrip)
            + " · first audio: " + latency(firstAudio)
            + "\nLast reconnect: #" + reconnectAttempt
            + " after " + reconnectDelay + " ms"
            + "\nRecoveries: " + recoveries
            + " · ordering warnings: " + invalid
            + "\nUpdated: " + time;
    }

    private static String latency(long value) {
        return value < 0L ? "not measured" : value + " ms";
    }

    private static String safe(String value) {
        return value == null || value.isBlank()
            ? "unknown"
            : value.trim();
    }
}
