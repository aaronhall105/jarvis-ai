package com.aaron.jarvisvoice;

import android.content.Context;
import android.os.Handler;
import android.os.Looper;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.IOException;
import java.util.ArrayList;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Set;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;

import okhttp3.HttpUrl;
import okhttp3.MediaType;
import okhttp3.OkHttpClient;
import okhttp3.Request;
import okhttp3.RequestBody;
import okhttp3.Response;

public final class ProactiveClient implements AutoCloseable {
    public interface FeedCallback {
        void onSuccess(List<ProactiveEvent> events, ProactiveSettings settings);
        void onError(String message);
    }

    public interface ResultCallback {
        void onSuccess();
        void onError(String message);
    }

    private static final MediaType JSON =
        MediaType.get("application/json; charset=utf-8");

    private final SecureStore store;
    private final Handler main = new Handler(Looper.getMainLooper());
    private final ExecutorService executor = Executors.newSingleThreadExecutor();
    private final OkHttpClient client = new OkHttpClient.Builder()
        .connectTimeout(4, TimeUnit.SECONDS)
        .readTimeout(8, TimeUnit.SECONDS)
        .callTimeout(12, TimeUnit.SECONDS)
        .retryOnConnectionFailure(false)
        .build();

    public ProactiveClient(Context context) {
        store = new SecureStore(context.getApplicationContext());
    }

    public void feed(FeedCallback callback) {
        executor.execute(() -> {
            try {
                JSONObject response = request(
                    "GET", "/api/proactive/feed", null, true
                );
                List<ProactiveEvent> events = new ArrayList<>();
                JSONArray source = response.optJSONArray("events");
                if (source != null) {
                    for (int index = 0; index < source.length(); index++) {
                        JSONObject item = source.optJSONObject(index);
                        if (item != null) events.add(ProactiveEvent.fromJson(item));
                    }
                }
                JSONObject rawSettings = response.optJSONObject("settings");
                ProactiveSettings settings = ProactiveSettings.fromJson(
                    rawSettings == null ? new JSONObject() : rawSettings,
                    store.userId()
                );
                main.post(() -> callback.onSuccess(events, settings));
            } catch (Exception exception) {
                main.post(() -> callback.onError(message(exception)));
            }
        });
    }

    public void save(ProactiveSettings settings, ResultCallback callback) {
        execute(
            "PUT", "/api/proactive/settings", settings.toJson(), callback
        );
    }

    public void action(
        ProactiveEvent event,
        String action,
        int minutes,
        ResultCallback callback
    ) {
        execute(
            "POST",
            "/api/proactive/events/" + event.id + "/action",
            new JSONObject().put("action", action).put("minutes", minutes),
            callback
        );
    }

    private void execute(
        String method,
        String path,
        JSONObject payload,
        ResultCallback callback
    ) {
        executor.execute(() -> {
            try {
                request(method, path, payload, false);
                main.post(callback::onSuccess);
            } catch (Exception exception) {
                main.post(() -> callback.onError(message(exception)));
            }
        });
    }

    private JSONObject request(
        String method,
        String path,
        JSONObject payload,
        boolean userQuery
    ) throws Exception {
        Exception last = null;
        for (String endpoint : endpoints()) {
            try {
                HttpUrl parsed = HttpUrl.parse(endpoint + path);
                if (parsed == null) continue;
                HttpUrl.Builder url = parsed.newBuilder();
                if (userQuery) url.addQueryParameter("user_id", store.userId());
                Request.Builder request = new Request.Builder()
                    .url(url.build())
                    .header("Authorization", "Bearer " + store.mobileToken())
                    .header("Accept", "application/json");
                if ("GET".equals(method)) {
                    request.get();
                } else {
                    RequestBody body = RequestBody.create(
                        payload == null ? "{}" : payload.toString(),
                        JSON
                    );
                    if ("PUT".equals(method)) request.put(body);
                    else request.post(body);
                }
                try (Response response = client.newCall(request.build()).execute()) {
                    String raw = response.body() == null
                        ? ""
                        : response.body().string();
                    if (!response.isSuccessful()) {
                        throw new IOException(
                            "Jarvis Core HTTP " + response.code()
                                + (raw.isBlank() ? "" : ": " + raw)
                        );
                    }
                    return raw.isBlank() ? new JSONObject() : new JSONObject(raw);
                }
            } catch (Exception exception) {
                last = exception;
            }
        }
        throw last == null
            ? new IOException("No Jarvis Core endpoint is available")
            : last;
    }

    private List<String> endpoints() {
        Set<String> values = new LinkedHashSet<>();
        String configured = CoreEndpointSelector.normaliseBaseUrl(store.coreUrl());
        if (!configured.isBlank()) values.add(configured);
        values.add(CoreEndpointSelector.DEFAULT_TAILSCALE_URL);
        return new ArrayList<>(values);
    }

    private static String message(Exception exception) {
        String value = exception.getMessage();
        return value == null || value.isBlank()
            ? exception.getClass().getSimpleName()
            : value;
    }

    @Override public void close() {
        executor.shutdownNow();
    }
}
