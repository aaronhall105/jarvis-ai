package com.aaron.jarvisvoice;

import android.content.Context;
import android.content.SharedPreferences;

import java.net.URI;
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

    public void recordNetworkStatus(boolean online, String detail) {
        preferences.edit()
            .putBoolean("network_online", online)
            .putString("network_detail", safe(detail))
            .putLong("network_updated_at", System.currentTimeMillis())
            .apply();
    }

    public void recordCoreReachability(String status, String detail) {
        preferences.edit()
            .putString("core_reachability", safe(status))
            .putString("core_reachability_detail", safe(detail))
            .putLong("core_reachability_updated_at", System.currentTimeMillis())
            .apply();
    }

    public void recordEndpoint(String name, String url) {
        preferences.edit()
            .putString("active_endpoint", safe(name))
            .putString("active_endpoint_url", endpointDisplay(url))
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

    public void recordInvalidTransition(
        String from,
        String to,
        String reason
    ) {
        int count = preferences.getInt(
            "invalid_transition_count",
            0
        );
        preferences.edit()
            .putInt("invalid_transition_count", count + 1)
            .putString(
                "last_invalid_transition",
                safe(from) + " -> " + safe(to) + " · " + safe(reason)
            )
            .apply();
    }

    public void recordCoreContext(
        String userName,
        String conversationId,
        int messageCount
    ) {
        preferences.edit()
            .putString("core_user", safe(userName))
            .putString(
                "conversation_id",
                shorten(conversationId, 70)
            )
            .putInt("message_count", Math.max(0, messageCount))
            .apply();
    }

    public void recordToolEvent(
        String tool,
        boolean success
    ) {
        preferences.edit()
            .putString("last_tool", safe(tool))
            .putBoolean("last_tool_success", success)
            .putLong("last_tool_at", System.currentTimeMillis())
            .apply();
    }

    public void recordMemoryContext(
        boolean used,
        int messageCount
    ) {
        preferences.edit()
            .putBoolean("memory_used", used)
            .putInt("message_count", Math.max(0, messageCount))
            .apply();
    }

    public void resetCounters() {
        preferences.edit()
            .putInt("recovery_count", 0)
            .putInt("invalid_transition_count", 0)
            .putInt("last_reconnect_attempt", 0)
            .putLong("last_reconnect_delay_ms", 0L)
            .remove("last_reconnect_reason")
            .remove("last_recovery")
            .remove("last_invalid_transition")
            .apply();
    }

    public String summary() {
        String state = preferences.getString("state", "Not started");
        String owner = preferences.getString("owner", "NONE");
        String audio = preferences.getString(
            "audio_processing",
            "Audio processing not measured yet"
        );
        boolean networkOnline = preferences.getBoolean(
            "network_online",
            false
        );
        String networkDetail = preferences.getString(
            "network_detail",
            preferences.getString("network", "Not measured")
        );
        String coreReachability = preferences.getString(
            "core_reachability",
            "Not measured"
        );
        String coreDetail = preferences.getString(
            "core_reachability_detail",
            "Not measured"
        );
        String endpoint = preferences.getString(
            "active_endpoint",
            "Not selected"
        );
        String endpointUrl = preferences.getString(
            "active_endpoint_url",
            "Not selected"
        );
        long connect = preferences.getLong("connect_latency_ms", -1L);
        long roundTrip = preferences.getLong("round_trip_ms", -1L);
        long firstAudio = preferences.getLong("first_audio_ms", -1L);
        int reconnectAttempt = preferences.getInt("last_reconnect_attempt", 0);
        long reconnectDelay = preferences.getLong("last_reconnect_delay_ms", 0L);
        int recoveries = preferences.getInt("recovery_count", 0);
        int invalid = preferences.getInt("invalid_transition_count", 0);
        String coreUser = preferences.getString("core_user", "Not synchronised");
        String conversationId = preferences.getString(
            "conversation_id",
            "Not synchronised"
        );
        int messageCount = preferences.getInt("message_count", 0);
        String lastTool = preferences.getString("last_tool", "None");
        boolean lastToolSuccess = preferences.getBoolean(
            "last_tool_success",
            false
        );
        boolean memoryUsed = preferences.getBoolean("memory_used", false);
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
            + "\nNetwork online: " + (networkOnline ? "yes" : "no")
            + " · " + networkDetail
            + "\nCore: " + coreReachability + " · " + coreDetail
            + "\nActive endpoint: " + endpoint
            + "\nCore address: " + endpointUrl
            + "\nConnect: " + latency(connect)
            + " · RTT: " + latency(roundTrip)
            + " · first audio: " + latency(firstAudio)
            + "\nCore context: " + coreUser
            + " · " + messageCount + " messages"
            + "\nConversation: " + conversationId
            + "\nMemory used last turn: " + (memoryUsed ? "yes" : "no")
            + "\nLast tool: " + lastTool
            + (
                "None".equals(lastTool)
                    ? ""
                    : lastToolSuccess ? " · success" : " · failed"
            )
            + "\nLast reconnect: #" + reconnectAttempt
            + " after " + reconnectDelay + " ms"
            + "\nRecoveries: " + recoveries
            + " · ordering warnings: " + invalid
            + "\nUpdated: " + time;
    }

    private static String endpointDisplay(String value) {
        String candidate = value == null ? "" : value.trim();
        try {
            URI uri = URI.create(candidate);
            String host = uri.getHost();
            int port = uri.getPort();
            if (host != null && !host.isBlank()) {
                return port > 0 ? host + ":" + port : host;
            }
        } catch (Exception ignored) {
        }
        return shorten(candidate, 100);
    }

    private static String latency(long value) {
        return value < 0L ? "not measured" : value + " ms";
    }

    private static String shorten(String value, int max) {
        String text = safe(value);
        if (text.length() <= max) return text;
        return text.substring(0, Math.max(1, max - 1)) + "…";
    }

    private static String safe(String value) {
        return value == null || value.isBlank()
            ? "unknown"
            : value.trim();
    }
}
