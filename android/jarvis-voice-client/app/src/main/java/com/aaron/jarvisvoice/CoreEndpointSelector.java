package com.aaron.jarvisvoice;

import android.content.Context;
import java.util.List;

public final class CoreEndpointSelector {
    public interface Listener {
        void onSelected(String url, String name);
        void onUnavailable(String reason);
    }

    private final CoreEndpointManager manager;
    private final String lanUrl;
    private final String remoteUrl;
    public CoreEndpointSelector(Context context, String lanUrl) {
        manager = CoreEndpointManager.get(context);
        this.lanUrl = normaliseBaseUrl(lanUrl);
        this.remoteUrl = normaliseOptionalBaseUrl(
            new SecureStore(context).remoteCoreUrl()
        );
    }

    public String lanUrl() {
        return lanUrl;
    }

    public String remoteUrl() {
        return remoteUrl;
    }

    public boolean isLan(String value) {
        return lanUrl.equals(normaliseBaseUrl(value));
    }

    public void select(Listener listener) {
        manager.select(callback(listener));
    }

    public void probeLan(Listener listener) {
        manager.probeConfiguredLocal(callback(listener));
    }

    public void cancel() {
        manager.cancelProbe();
    }

    static String normaliseBaseUrl(String value) {
        String candidate = value == null ? "" : value.trim();
        while (candidate.endsWith("/")) {
            candidate = candidate.substring(0, candidate.length() - 1);
        }
        return candidate;
    }

    static String normaliseOptionalBaseUrl(String value) {
        String candidate = value == null ? "" : value.trim();
        while (candidate.endsWith("/")) {
            candidate = candidate.substring(0, candidate.length() - 1);
        }
        return candidate;
    }

    static String healthUrl(String value) {
        return normaliseBaseUrl(value) + "/health/live";
    }

    static List<String> preferenceOrder(
        boolean localTransport,
        String lan,
        String remote
    ) {
        return EndpointRoutePolicy.order(
            localTransport,
            normaliseBaseUrl(lan),
            normaliseOptionalBaseUrl(remote)
        );
    }

    static List<String> candidateUrls(Context context, String lan, String remote) {
        return CoreEndpointManager.get(context).candidates();
    }

    private CoreEndpointManager.SelectionCallback callback(Listener listener) {
        return new CoreEndpointManager.SelectionCallback() {
            @Override public void onSelected(String endpoint, String name) {
                listener.onSelected(endpoint, name);
            }

            @Override public void onUnavailable(String message) {
                listener.onUnavailable(message);
            }
        };
    }
}
