package com.aaron.jarvisvoice;

import android.content.Context;
import android.net.ConnectivityManager;
import android.net.Network;
import android.net.NetworkCapabilities;
import android.os.Handler;
import android.os.Looper;

import java.util.concurrent.TimeUnit;
import java.util.List;

import okhttp3.Call;
import okhttp3.Callback;
import okhttp3.OkHttpClient;
import okhttp3.Request;
import okhttp3.Response;

public final class CoreEndpointSelector {
    public interface Listener {
        void onSelected(String url, String name);
        void onUnavailable(String reason);
    }

    private final ConnectivityManager connectivity;
    private final Handler main = new Handler(Looper.getMainLooper());
    private final String lanUrl;
    private final String remoteUrl;
    private final OkHttpClient healthClient = new OkHttpClient.Builder()
        .connectTimeout(2200, TimeUnit.MILLISECONDS)
        .readTimeout(2200, TimeUnit.MILLISECONDS)
        .callTimeout(3000, TimeUnit.MILLISECONDS)
        .retryOnConnectionFailure(false)
        .build();

    private Call activeProbe;

    public CoreEndpointSelector(Context context, String lanUrl) {
        connectivity = (ConnectivityManager) context
            .getApplicationContext()
            .getSystemService(Context.CONNECTIVITY_SERVICE);
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
        cancel();
        List<String> order = preferenceOrder(hasLocalTransport(), lanUrl, remoteUrl);
        if (order.size() == 1) {
            probe(order.get(0), endpointName(order.get(0)), listenerResult(listener));
            return;
        }
        probeThen(order.get(0), endpointName(order.get(0)), order.get(1), endpointName(order.get(1)), listener);
    }

    public void probeLan(Listener listener) {
        cancel();
        probe(lanUrl, "LAN", listenerResult(listener));
    }

    public void cancel() {
        Call probe = activeProbe;
        activeProbe = null;
        if (probe != null) probe.cancel();
    }

    static String normaliseBaseUrl(String value) {
        String candidate = value == null ? "" : value.trim();
        while (candidate.endsWith("/")) {
            candidate = candidate.substring(0, candidate.length() - 1);
        }
        return candidate.isBlank()
            ? "http://192.168.1.40:8000"
            : candidate;
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

    private String endpointName(String url) { return lanUrl.equals(url) ? "LAN" : "Remote"; }

    private ProbeResult listenerResult(Listener listener) {
        return new ProbeResult() {
            @Override public void reachable(String url, String name) {
                listener.onSelected(url, name);
            }
            @Override public void unreachable(String reason) {
                listener.onUnavailable(reason);
            }
        };
    }

    private void probeThen(
        String firstUrl,
        String firstName,
        String secondUrl,
        String secondName,
        Listener listener
    ) {
        probe(firstUrl, firstName, new ProbeResult() {
            @Override public void reachable(String url, String name) {
                listener.onSelected(url, name);
            }

            @Override public void unreachable(String firstReason) {
                probe(secondUrl, secondName, new ProbeResult() {
                    @Override public void reachable(String url, String name) {
                        listener.onSelected(url, name);
                    }

                    @Override public void unreachable(String secondReason) {
                        listener.onUnavailable(
                            firstName + ": " + firstReason
                                + "; " + secondName + ": " + secondReason
                        );
                    }
                });
            }
        });
    }

    private void probe(String url, String name, ProbeResult result) {
        final Request request;
        try {
            String checked = CoreUrl.validateBase(url);
            request = new Request.Builder()
                .url(healthUrl(checked))
                .get()
                .build();
        } catch (Exception exception) {
            post(() -> result.unreachable(safeMessage(exception)));
            return;
        }

        Call call = healthClient.newCall(request);
        activeProbe = call;
        call.enqueue(new Callback() {
            @Override public void onFailure(Call ignored, java.io.IOException exception) {
                if (activeProbe == call) activeProbe = null;
                post(() -> result.unreachable(safeMessage(exception)));
            }

            @Override public void onResponse(Call ignored, Response response) {
                boolean healthy;
                int code;
                try (response) {
                    code = response.code();
                    healthy = response.isSuccessful();
                }
                if (activeProbe == call) activeProbe = null;
                if (healthy) {
                    post(() -> result.reachable(url, name));
                } else {
                    post(() -> result.unreachable("HTTP " + code));
                }
            }
        });
    }

    private boolean hasLocalTransport() {
        if (connectivity == null) return false;
        Network network = connectivity.getActiveNetwork();
        NetworkCapabilities capabilities = connectivity.getNetworkCapabilities(network);
        return NetworkQualityMonitor.isLocalTransport(
            capabilities
        );
    }

    private void post(Runnable runnable) {
        main.post(runnable);
    }

    private static String safeMessage(Exception exception) {
        String message = exception.getMessage();
        return message == null || message.isBlank()
            ? exception.getClass().getSimpleName()
            : message;
    }

    private interface ProbeResult {
        void reachable(String url, String name);
        void unreachable(String reason);
    }
}
