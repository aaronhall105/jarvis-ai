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
        .followRedirects(false).followSslRedirects(false).retryOnConnectionFailure(false)
        .callTimeout(20, TimeUnit.SECONDS).build();
    private final ExecutorService executor = Executors.newSingleThreadExecutor();
    private final Handler main = new Handler(Looper.getMainLooper());
    private final SecureStore store;
    private volatile boolean closed;
    AutonomyClient(SecureStore store) { this.store = store; }

    void observe(Callback callback) { request("/observe", new JSONObject(), callback); }

    void preparation(int candidateId, Callback callback) {
        if (candidateId < 1) { deliver(callback, null, "Invalid candidate identity."); return; }
        request("/development/prepare/" + candidateId, null, callback);
    }

    void activation(String taskId, Callback callback) { taskPreview("activation", taskId, callback); }
    void proposal(String taskId, Callback callback) { taskPreview("proposal", taskId, callback); }

    private void taskPreview(String kind, String taskId, Callback callback) {
        try { DevelopmentRequestStore.requireId(taskId); request("/development/" + kind + "/" + taskId, null, callback); }
        catch (IllegalArgumentException invalid) { deliver(callback, null, "Invalid protected task identity."); }
    }

    void prepare(JSONObject request, Callback callback) { signedProduct("/development/start", request, callback); }
    void activate(JSONObject request, Callback callback) { signedProduct("/development/activate", request, callback); }
    void publish(JSONObject request, Callback callback) { signedProduct("/development/publish", request, callback); }

    private void signedProduct(String path, JSONObject request, Callback callback) {
        executor.execute(() -> {
            try {
                JSONObject signed = switch (path) {
                    case "/development/start" -> AutonomyControl.signedPreparation(request);
                    case "/development/activate" -> AutonomyControl.signedActivation(request);
                    case "/development/publish" -> AutonomyControl.signedIntegration(request);
                    default -> throw new IllegalArgumentException("Unsupported authority");
                };
                execute(path, signed, callback);
            } catch (Exception invalid) {
                deliver(callback, null, "The protected request is expired or invalid. Refresh before confirming again.");
            }
        });
    }

    void control(String action, String generation, Callback callback) {
        executor.execute(() -> {
            try { execute("/control", AutonomyControl.signed(action, generation), callback); }
            catch (Exception ignored) { deliver(callback, null, "Operator authorization is not available."); }
        });
    }

    void approveDevelopment(JSONObject task, Callback callback) {
        executor.execute(() -> {
            try { execute("/control", AutonomyControl.signedDevelopment(task), callback); }
            catch (Exception ignored) {
                deliver(callback, null, "The development approval could not be created. Nothing was authorized.");
            }
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
                Request.Builder builder = new Request.Builder().url(endpoint + "/api/autonomy" + path)
                    .header("Authorization", "Bearer " + store.developerToken());
                Request request = (body == null ? builder.get() : builder.post(
                    RequestBody.create(body.toString(), MediaType.get("application/json")))).build();
                try (Response response = http.newCall(request).execute()) {
                    if (response.code() == 410) {
                        deliver(callback, null, "This development approval expired. Refresh for a new task."); return;
                    }
                    if (response.code() == 401) {
                        deliver(callback, null, "Developer authentication is required. Check the existing connection settings."); return;
                    }
                    if (response.code() == 403) {
                        deliver(callback, null, "Development approval was rejected. No state change was confirmed."); return;
                    }
                    if (response.code() == 409) {
                        deliver(callback, null, "Protected state changed or this request was already used. Refresh its recorded result."); return;
                    }
                    if (response.code() == 503 && !AutonomyPresentation.retryHttpFailure(path)) {
                        deliver(callback, null, "Protected operator service is unavailable. No approval confirmation was received; refresh before retrying."); return;
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
                    JSONObject result = new JSONObject(text);
                    if (path.startsWith("/development/") && body != null
                        && !AutonomyPresentation.productAcknowledged(path, body, result)) {
                        deliver(callback, null, "Protected confirmation did not match this request. Refresh its recorded state before continuing."); return;
                    }
                    deliver(callback, result, null); return;
                }
            } catch (Exception ignored) {
                // No tokens, response bodies, host errors or URLs are logged.
                if (!AutonomyPresentation.retryHttpFailure(path)) {
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
