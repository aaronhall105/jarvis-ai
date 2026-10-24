package com.aaron.jarvisvoice;

import android.os.Handler;
import android.os.Looper;
import java.util.List;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;
import okhttp3.MediaType;
import okhttp3.OkHttpClient;
import okhttp3.Request;
import okhttp3.RequestBody;
import okhttp3.Response;
import org.json.JSONObject;

/** Uses existing Developer endpoint/auth settings; no new shared resume secret. */
final class AutonomyClient implements AutoCloseable {
    interface Callback { void done(JSONObject state, String error); }
    private final OkHttpClient http = new OkHttpClient.Builder()
        .followRedirects(false).followSslRedirects(false)
        .callTimeout(20, TimeUnit.SECONDS).build();
    private final ExecutorService executor = Executors.newSingleThreadExecutor();
    private final Handler main = new Handler(Looper.getMainLooper());
    private final SecureStore store;
    private volatile boolean closed;
    AutonomyClient(SecureStore store) { this.store = store; }

    void observe(Callback callback) { request("/observe", new JSONObject(), callback); }

    void control(String action, String generation, Callback callback) {
        executor.execute(() -> {
            try { execute("/control", AutonomyControl.signed(action, generation), callback); }
            catch (Exception ignored) { deliver(callback, null, "Operator authorization is not available."); }
        });
    }

    private void request(String path, JSONObject body, Callback callback) {
        executor.execute(() -> execute(path, body, callback));
    }

    private void execute(String path, JSONObject body, Callback callback) {
        List<String> endpoints = DeveloperEndpointPolicy.order(false, store.developerUrl(), store.remoteDeveloperUrl());
        if (store.developerToken().isBlank()) {
            deliver(callback, null, "Connect Developer Mode in Settings first."); return;
        }
        for (String endpoint : endpoints) {
            if (closed) return;
            try {
                Request request = new Request.Builder().url(endpoint + "/api/autonomy" + path)
                    .header("Authorization", "Bearer " + store.developerToken())
                    .post(RequestBody.create(body.toString(), MediaType.get("application/json"))).build();
                try (Response response = http.newCall(request).execute()) {
                    if (response.code() == 401 || response.code() == 403) {
                        deliver(callback, null, "Operator authorization is required. No state change was confirmed."); return;
                    }
                    if (!response.isSuccessful() || response.body() == null) {
                        if (!AutonomyPresentation.retryHttpFailure(path)) {
                            deliver(callback, null, "Control outcome is unknown. Refresh status before trying again."); return;
                        }
                        continue;
                    }
                    okio.BufferedSource source = response.body().source();
                    if (source.request(262145)) throw new IllegalStateException("Response too large");
                    String text = source.readUtf8();
                    deliver(callback, new JSONObject(text), null); return;
                }
            } catch (Exception ignored) {
                // No tokens, response bodies, host errors or URLs are logged.
                if (path.equals("/control")) {
                    deliver(callback, null, "Control outcome is unknown. Refresh status before trying again."); return;
                }
            }
        }
        deliver(callback, null, "Development observation is not available yet. Jarvis is still running normally.");
    }

    private void deliver(Callback callback, JSONObject body, String error) {
        main.post(() -> { if (!closed) callback.done(body, error); });
    }
    @Override public void close() {
        closed = true; http.dispatcher().cancelAll(); executor.shutdownNow();
        http.connectionPool().evictAll();
    }
}
