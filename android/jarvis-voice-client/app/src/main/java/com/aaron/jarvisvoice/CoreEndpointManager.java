package com.aaron.jarvisvoice;

import android.content.Context;
import android.net.ConnectivityManager;
import android.net.Network;
import android.net.NetworkCapabilities;
import android.os.Handler;
import android.os.Looper;

import org.json.JSONObject;

import java.io.IOException;
import java.util.ArrayList;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.concurrent.CopyOnWriteArrayList;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.TimeUnit;

import okhttp3.Call;
import okhttp3.Callback;
import okhttp3.OkHttpClient;
import okhttp3.Request;
import okhttp3.Response;

/** One process-wide authority for every Jarvis Core HTTP and WebSocket client. */
public final class CoreEndpointManager {
    public enum State { CONNECTED, PROBING, FAILOVER, OFFLINE, RECOVERING }

    public record Snapshot(State state, String endpoint, String name, String detail) {}

    public interface Listener {
        void onEndpointStateChanged(Snapshot snapshot);
    }

    public interface SelectionCallback {
        void onSelected(String endpoint, String name);
        void onUnavailable(String userMessage);
    }

    private static CoreEndpointManager instance;
    private static final long FAILED_ENDPOINT_COOLDOWN_MS = 120_000L;

    public static synchronized CoreEndpointManager get(Context context) {
        if (instance == null) {
            instance = new CoreEndpointManager(context.getApplicationContext());
        }
        return instance;
    }

    static synchronized void resetForTests() {
        if (instance != null) instance.cancelProbe();
        instance = null;
    }

    private final Context context;
    private final SecureStore store;
    private final ConnectivityManager connectivity;
    private final Handler main = new Handler(Looper.getMainLooper());
    private final CopyOnWriteArrayList<Listener> listeners = new CopyOnWriteArrayList<>();
    private final ConcurrentHashMap<String, Long> retryAfter = new ConcurrentHashMap<>();
    private final OkHttpClient healthClient = new OkHttpClient.Builder()
        .connectTimeout(2200, TimeUnit.MILLISECONDS)
        .readTimeout(2200, TimeUnit.MILLISECONDS)
        .callTimeout(3000, TimeUnit.MILLISECONDS)
        .retryOnConnectionFailure(false)
        .build();

    private volatile Snapshot snapshot = new Snapshot(State.OFFLINE, "", "", "Not checked");
    private volatile Call activeProbe;

    private CoreEndpointManager(Context context) {
        this.context = context;
        store = new SecureStore(context);
        connectivity = (ConnectivityManager) context.getSystemService(Context.CONNECTIVITY_SERVICE);
    }

    public Snapshot snapshot() { return snapshot; }

    public void addListener(Listener listener) {
        if (listener == null) return;
        listeners.addIfAbsent(listener);
        listener.onEndpointStateChanged(snapshot);
    }

    public void removeListener(Listener listener) { listeners.remove(listener); }

    /**
     * Candidates are restricted to explicit app configuration. A persisted
     * last-known-good value is accepted only while it still matches one of
     * those configured endpoints, so stored/network input cannot create SSRF.
     */
    public List<String> candidates() {
        List<String> ordered = orderedCandidates(
            hasLocalTransport(),
            store.coreUrl(),
            store.remoteCoreUrl(),
            snapshot.endpoint(),
            store.lastGoodCoreUrl()
        );
        ArrayList<String> available = new ArrayList<>();
        ArrayList<String> cooling = new ArrayList<>();
        for (String value : ordered) {
            if (isCoolingDown(value)) cooling.add(value);
            else available.add(value);
        }
        if (available.isEmpty()) return ordered;
        ArrayList<String> output = new ArrayList<>(available);
        output.addAll(cooling);
        return List.copyOf(output);
    }

    static List<String> orderedCandidates(
        boolean localTransport,
        String local,
        String remote,
        String active,
        String lastGood
    ) {
        String localValue = validConfigured(local);
        String remoteValue = validConfigured(remote);
        List<String> configured = EndpointRoutePolicy.order(localTransport, localValue, remoteValue);
        LinkedHashSet<String> output = new LinkedHashSet<>();
        String activeValue = normalise(active);
        String lastGoodValue = normalise(lastGood);
        if (configured.contains(activeValue)) output.add(activeValue);
        if (configured.contains(lastGoodValue)) output.add(lastGoodValue);
        output.addAll(configured);
        return List.copyOf(output);
    }

    public void select(SelectionCallback callback) {
        cancelProbe();
        List<String> candidates = candidates();
        if (candidates.isEmpty()) {
            update(State.OFFLINE, "", "", "No Core endpoint is configured");
            post(() -> callback.onUnavailable("Jarvis Core is not configured."));
            return;
        }
        update(snapshot.endpoint().isBlank() ? State.PROBING : State.RECOVERING,
            snapshot.endpoint(), snapshot.name(), "Checking configured endpoints");
        probeAt(candidates, 0, callback, new ArrayList<>());
    }

    public void probeConfiguredLocal(SelectionCallback callback) {
        String local = validConfigured(store.coreUrl());
        if (local.isBlank()) {
            post(() -> callback.onUnavailable("No LAN endpoint is configured."));
            return;
        }
        if (isCoolingDown(local)) {
            post(() -> callback.onUnavailable("LAN endpoint is cooling down after a failure."));
            return;
        }
        cancelProbe();
        probeAt(List.of(local), 0, callback, new ArrayList<>());
    }

    public void reportSuccess(String endpoint) {
        String selected = normalise(endpoint);
        if (!candidates().contains(selected)) return;
        retryAfter.remove(selected);
        store.setLastGoodCoreUrl(selected);
        update(State.CONNECTED, selected, endpointName(selected), "Verified Jarvis Core");
    }

    private void reportCompatible(String endpoint) {
        String selected = normalise(endpoint);
        if (!candidates().contains(selected)) return;
        update(State.RECOVERING, selected, endpointName(selected), "Compatible Jarvis Core found");
    }

    public void reportTransportFailure(String endpoint) {
        String failed = normalise(endpoint);
        if (!candidates().contains(failed)) return;
        retryAfter.put(failed, System.currentTimeMillis() + FAILED_ENDPOINT_COOLDOWN_MS);
        if (!failed.equals(snapshot.endpoint())) return;
        update(State.FAILOVER, failed, endpointName(failed), "Connection lost; trying another endpoint");
    }

    public void reportOffline() {
        update(State.OFFLINE, snapshot.endpoint(), snapshot.name(), "Jarvis Core is unreachable");
    }

    public synchronized void cancelProbe() {
        Call probe = activeProbe;
        activeProbe = null;
        if (probe != null) probe.cancel();
    }

    private void probeAt(
        List<String> values,
        int index,
        SelectionCallback callback,
        List<String> failures
    ) {
        if (index >= values.size()) {
            reportOffline();
            post(() -> callback.onUnavailable("Can't reach Jarvis Core."));
            return;
        }
        String endpoint = values.get(index);
        Request request;
        try {
            request = new Request.Builder().url(endpoint + "/health").get().build();
        } catch (Exception exception) {
            failures.add(endpointName(endpoint));
            probeAt(values, index + 1, callback, failures);
            return;
        }
        Call call = healthClient.newCall(request);
        activeProbe = call;
        call.enqueue(new Callback() {
            @Override public void onFailure(Call ignored, IOException exception) {
                if (activeProbe == call) activeProbe = null;
                retryAfter.put(
                    endpoint,
                    System.currentTimeMillis() + FAILED_ENDPOINT_COOLDOWN_MS
                );
                failures.add(endpointName(endpoint));
                update(State.FAILOVER, endpoint, endpointName(endpoint), "Trying another configured endpoint");
                probeAt(values, index + 1, callback, failures);
            }

            @Override public void onResponse(Call ignored, Response response) {
                boolean compatible = false;
                try (response) {
                    if (response.isSuccessful() && response.body() != null) {
                        JSONObject health = new JSONObject(response.body().string());
                        compatible = "healthy".equalsIgnoreCase(health.optString("status"))
                            && !health.optString("service").isBlank()
                            && !health.optString("version").isBlank();
                    }
                } catch (Exception ignoredException) {
                    compatible = false;
                }
                if (activeProbe == call) activeProbe = null;
                if (compatible) {
                    reportCompatible(endpoint);
                    post(() -> callback.onSelected(endpoint, endpointName(endpoint)));
                } else {
                    failures.add(endpointName(endpoint));
                    update(State.FAILOVER, endpoint, endpointName(endpoint), "Endpoint is not a compatible Jarvis Core");
                    probeAt(values, index + 1, callback, failures);
                }
            }
        });
    }

    private boolean hasLocalTransport() {
        if (connectivity == null) return false;
        Network network = connectivity.getActiveNetwork();
        NetworkCapabilities capabilities = connectivity.getNetworkCapabilities(network);
        return NetworkQualityMonitor.isLocalTransport(capabilities);
    }

    private String endpointName(String endpoint) {
        String local = normalise(store.coreUrl());
        String remote = normalise(store.remoteCoreUrl());
        if (endpoint.equals(local)) return "LAN";
        if (endpoint.equals(remote)) return "Remote";
        return "Core";
    }

    private void update(State state, String endpoint, String name, String detail) {
        Snapshot next = new Snapshot(state, normalise(endpoint), name == null ? "" : name,
            detail == null ? "" : detail);
        snapshot = next;
        for (Listener listener : listeners) post(() -> listener.onEndpointStateChanged(next));
    }

    private void post(Runnable runnable) { main.post(runnable); }

    boolean isCoolingDown(String endpoint) {
        String candidate = normalise(endpoint);
        Long until = retryAfter.get(candidate);
        if (until == null) return false;
        if (until <= System.currentTimeMillis()) {
            retryAfter.remove(candidate, until);
            return false;
        }
        return true;
    }

    private static String validConfigured(String value) {
        String candidate = normalise(value);
        if (candidate.isBlank()) return "";
        try { return CoreUrl.validateBase(candidate); }
        catch (Exception ignored) { return ""; }
    }

    private static String normalise(String value) {
        String candidate = value == null ? "" : value.trim();
        while (candidate.endsWith("/")) candidate = candidate.substring(0, candidate.length() - 1);
        return candidate;
    }
}
