package com.aaron.jarvisvoice;

import android.content.Context;
import android.os.Handler;
import android.os.Looper;
import android.os.SystemClock;
import android.util.Log;

import org.json.JSONObject;

import java.io.IOException;
import java.util.UUID;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;

import okhttp3.HttpUrl;
import okhttp3.MediaType;
import okhttp3.OkHttpClient;
import okhttp3.Request;
import okhttp3.RequestBody;
import okhttp3.Response;

/** Authenticated HomeExperience client using Core endpoint failover and safe caching. */
final class HomeClient implements AutoCloseable {
    private static final String PERFORMANCE_TAG = "JarvisHomePerf";
    interface HomeCallback {
        void onSuccess(HomeExperience home, boolean fromCache, long receivedAtMillis);
        void onError(String message);
    }

    interface ActionCallback {
        void onSuccess(JSONObject result);
        void onError(String message);
    }

    private static final MediaType JSON = MediaType.get("application/json; charset=utf-8");
    private final SecureStore store;
    private final CoreEndpointManager endpoints;
    private final HomeSnapshotStore snapshots;
    private final Handler main = new Handler(Looper.getMainLooper());
    private final ExecutorService executor = Executors.newSingleThreadExecutor();
    private final OkHttpClient client = new OkHttpClient.Builder()
        .connectTimeout(5, TimeUnit.SECONDS)
        .readTimeout(20, TimeUnit.SECONDS)
        .callTimeout(25, TimeUnit.SECONDS)
        .retryOnConnectionFailure(false)
        .build();

    HomeClient(Context context) {
        Context application = context.getApplicationContext();
        store = new SecureStore(application);
        endpoints = CoreEndpointManager.get(application);
        snapshots = new HomeSnapshotStore(application, store.userId());
    }

    void fetch(HomeCallback callback) {
        executor.execute(() -> {
            HomeSnapshotStore.Snapshot cached = snapshots.load();
            try {
                FetchResult result = requestHome(cached == null ? "" : cached.etag());
                long now = System.currentTimeMillis();
                if (result.notModified) {
                    if (cached == null) throw new IOException("Home cache is unavailable");
                    snapshots.touch(now);
                    HomeExperience home = parse(cached.response());
                    main.post(() -> callback.onSuccess(home, false, now));
                    return;
                }
                HomeExperience home = parse(result.body);
                snapshots.save(result.body, result.etag, now);
                main.post(() -> callback.onSuccess(home, false, now));
            } catch (Exception exception) {
                main.post(() -> callback.onError(userMessage(exception)));
            }
        });
    }

    HomeSnapshotStore.Snapshot cached() { return snapshots.load(); }

    void execute(String actionId, ActionCallback callback) {
        executor.execute(() -> {
            try {
                JSONObject payload = new JSONObject()
                    .put("request_id", UUID.randomUUID().toString());
                JSONObject result = postAction(actionId, payload);
                main.post(() -> callback.onSuccess(result));
            } catch (Exception exception) {
                main.post(() -> callback.onError(userMessage(exception)));
            }
        });
    }

    private record FetchResult(JSONObject body, String etag, boolean notModified) {}

    private static HomeExperience parse(JSONObject value) {
        long started = SystemClock.elapsedRealtimeNanos();
        HomeExperience home = HomeExperience.fromJson(value);
        double elapsedMs = (SystemClock.elapsedRealtimeNanos() - started) / 1_000_000.0;
        Log.d(PERFORMANCE_TAG, String.format(java.util.Locale.ROOT, "parse_ms=%.3f", elapsedMs));
        return home;
    }

    private FetchResult requestHome(String etag) throws Exception {
        IOException last = null;
        for (String endpoint : endpoints.candidates()) {
            try {
                HttpUrl base = HttpUrl.parse(endpoint);
                if (base == null) continue;
                HttpUrl url = base.newBuilder()
                    .addPathSegments("api/home")
                    .build();
                Request.Builder request = authenticated(new Request.Builder().url(url).get());
                if (etag != null && !etag.isBlank()) request.header("If-None-Match", etag);
                try (Response response = client.newCall(request.build()).execute()) {
                    if (response.code() == 304) {
                        endpoints.reportSuccess(endpoint);
                        return new FetchResult(new JSONObject(), etag, true);
                    }
                    String raw = response.body() == null ? "" : response.body().string();
                    if (!response.isSuccessful()) throw responseError(raw, "Home request failed");
                    endpoints.reportSuccess(endpoint);
                    return new FetchResult(
                        raw.isBlank() ? new JSONObject() : new JSONObject(raw),
                        response.header("ETag", ""),
                        false
                    );
                }
            } catch (IOException exception) {
                endpoints.reportTransportFailure(endpoint);
                last = exception;
            }
        }
        endpoints.reportOffline();
        throw last == null ? new IOException("Jarvis Core could not be reached") : last;
    }

    private JSONObject postAction(String actionId, JSONObject payload) throws Exception {
        IOException last = null;
        for (String endpoint : endpoints.candidates()) {
            try {
                HttpUrl base = HttpUrl.parse(endpoint);
                if (base == null) continue;
                HttpUrl url = base.newBuilder()
                    .addPathSegments("api/home/actions")
                    .addPathSegment(actionId)
                    .build();
                Request request = authenticated(new Request.Builder().url(url))
                    .post(RequestBody.create(payload.toString(), JSON))
                    .build();
                try (Response response = client.newCall(request).execute()) {
                    String raw = response.body() == null ? "" : response.body().string();
                    if (!response.isSuccessful()) throw responseError(raw, "Home action failed");
                    endpoints.reportSuccess(endpoint);
                    return raw.isBlank() ? new JSONObject() : new JSONObject(raw);
                }
            } catch (IOException exception) {
                endpoints.reportTransportFailure(endpoint);
                last = exception;
            }
        }
        endpoints.reportOffline();
        throw last == null ? new IOException("Jarvis Core could not be reached") : last;
    }

    private Request.Builder authenticated(Request.Builder request) throws IOException {
        String token = store.mobileToken();
        if (token.isBlank()) throw new IOException("Mobile voice token is not configured");
        return request
            .header("Authorization", "Bearer " + token)
            .header("Accept", "application/json");
    }

    private static IOException responseError(String raw, String fallback) {
        String detail = raw;
        try { detail = new JSONObject(raw).optString("detail", raw); }
        catch (Exception ignored) { }
        return new IOException(detail == null || detail.isBlank() ? fallback : detail);
    }

    private static String userMessage(Exception exception) {
        String message = exception.getMessage();
        if (message != null && !message.isBlank() && !message.contains("http")) return message;
        return "Can't reach Jarvis Core.";
    }

    @Override public void close() { executor.shutdownNow(); }
}
