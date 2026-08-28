package com.aaron.jarvisvoice;

import org.json.JSONArray;
import org.json.JSONObject;

import java.util.ArrayList;
import java.util.List;

/** Redacted provider state returned by Jarvis Core. */
public final class IntegrationProvider {
    public final String id;
    public final String name;
    public final String state;
    public final boolean connected;
    public final boolean healthy;
    public final String healthReason;
    public final List<String> setupRequirements;
    public final List<String> grantedCapabilities;
    public final String accountId;
    public final boolean canConnect;
    public final boolean canReconnect;
    public final boolean canDisconnect;

    public IntegrationProvider(
        String id,
        String name,
        String state,
        boolean connected,
        boolean healthy,
        String healthReason,
        List<String> setupRequirements,
        List<String> grantedCapabilities,
        String accountId,
        boolean canConnect,
        boolean canReconnect,
        boolean canDisconnect
    ) {
        this.id = id;
        this.name = name;
        this.state = state;
        this.connected = connected;
        this.healthy = healthy;
        this.healthReason = healthReason;
        this.setupRequirements = List.copyOf(setupRequirements);
        this.grantedCapabilities = List.copyOf(grantedCapabilities);
        this.accountId = accountId;
        this.canConnect = canConnect;
        this.canReconnect = canReconnect;
        this.canDisconnect = canDisconnect;
    }

    public static IntegrationProvider fromJson(JSONObject value) {
        JSONObject account = value.optJSONObject("account");
        return new IntegrationProvider(
            value.optString("provider_id", ""),
            value.optString("name", "Integration"),
            value.optString("state", "Setup required"),
            value.optBoolean("connected", false),
            value.optBoolean("healthy", false),
            value.optString("health_reason", ""),
            strings(value.optJSONArray("setup_requirements")),
            strings(value.optJSONArray("granted_capabilities")),
            account == null ? "" : account.optString("account_id", ""),
            value.optBoolean("can_connect", false),
            value.optBoolean("can_reconnect", false),
            value.optBoolean("can_disconnect", false)
        );
    }

    private static List<String> strings(JSONArray values) {
        List<String> output = new ArrayList<>();
        if (values == null) return output;
        for (int index = 0; index < values.length(); index++) {
            String value = values.optString(index, "").trim();
            if (!value.isBlank()) output.add(value);
        }
        return output;
    }

    public String detail() {
        if (!healthReason.isBlank()) return healthReason;
        if (!setupRequirements.isEmpty()) return setupRequirements.get(0);
        if ("Partial permissions".equals(state)) {
            return grantedCapabilities.size() + " capabilities currently granted";
        }
        return healthy ? "Provider health verified by Jarvis Core" : "Not available to Jarvis";
    }
}
