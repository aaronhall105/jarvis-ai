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

/** Authenticated client for the redacted mobile integrations API. */
public final class IntegrationsClient implements AutoCloseable {
    public interface ProvidersCallback {
        void onSuccess(List<IntegrationProvider> providers);
        void onError(String message);
    }

    public interface OAuthCallback {
        void onSuccess(String authorizationUrl);
        void onError(String message);
    }

    public interface ResultCallback {
        void onSuccess();
        void onError(String message);
    }

    private static final MediaType JSON = MediaType.get("application/json; charset=utf-8");
    private final SecureStore store;
    private final Handler main = new Handler(Looper.getMainLooper());
    private final ExecutorService executor = Executors.newSingleThreadExecutor();
    private final OkHttpClient client = new OkHttpClient.Builder()
        .connectTimeout(5, TimeUnit.SECONDS)
        .readTimeout(15, TimeUnit.SECONDS)
        .callTimeout(20, TimeUnit.SECONDS)
        .retryOnConnectionFailure(false)
        .build();

    public IntegrationsClient(Context context) {
        store = new SecureStore(context.getApplicationContext());
    }

    public void providers(ProvidersCallback callback) {
        executor.execute(() -> {
            try {
                JSONObject response = request(
                    "GET", "/api/integrations/mobile/providers?refresh=true", null
                );
                List<IntegrationProvider> providers = new ArrayList<>();
                JSONArray values = response.optJSONArray("providers");
                if (values != null) {
                    for (int index = 0; index < values.length(); index++) {
                        JSONObject value = values.optJSONObject(index);
                        if (value != null) providers.add(IntegrationProvider.fromJson(value));
                    }
                }
                main.post(() -> callback.onSuccess(providers));
            } catch (Exception exception) {
                main.post(() -> callback.onError(message(exception)));
            }
        });
    }

    public void startGoogle(OAuthCallback callback) {
        executor.execute(() -> {
            try {
                JSONObject payload = new JSONObject();
                payload.put("features", new JSONArray(List.of(
                    "gmail_read",
                    "gmail_write",
                    "calendar_read",
                    "calendar_write",
                    "contacts_read"
                )));
                JSONObject response = request(
                    "POST", "/api/integrations/mobile/google/start", payload
                );
                String url = response.optString("authorization_url", "").trim();
                if (!isGoogleAuthorizationUrl(url)) {
                    throw new IOException("Core returned an invalid Google authorization URL");
                }
                main.post(() -> callback.onSuccess(url));
            } catch (Exception exception) {
                main.post(() -> callback.onError(message(exception)));
            }
        });
    }

    public void disconnectGoogle(String accountId, ResultCallback callback) {
        if (accountId == null || accountId.isBlank()) {
            callback.onError("No connected Google account was returned by Core");
            return;
        }
        executor.execute(() -> {
            try {
                request(
                    "DELETE",
                    "/api/integrations/mobile/google/accounts/" + accountId,
                    null
                );
                main.post(callback::onSuccess);
            } catch (Exception exception) {
                main.post(() -> callback.onError(message(exception)));
            }
        });
    }

    private JSONObject request(String method, String path, JSONObject payload) throws Exception {
        if (store.mobileToken().isBlank()) {
            throw new IOException("Mobile voice token is not configured");
        }
        Exception last = null;
        for (String endpoint : endpoints()) {
            try {
                HttpUrl url = HttpUrl.parse(endpoint + path);
                if (url == null) continue;
                Request.Builder request = new Request.Builder()
                    .url(url)
                    .header("Authorization", "Bearer " + store.mobileToken())
                    .header("Accept", "application/json");
                if ("GET".equals(method)) {
                    request.get();
                } else if ("DELETE".equals(method)) {
                    request.delete();
                } else {
                    request.post(RequestBody.create(
                        payload == null ? "{}" : payload.toString(),
                        JSON
                    ));
                }
                try (Response response = client.newCall(request.build()).execute()) {
                    String raw = response.body() == null ? "" : response.body().string();
                    if (!response.isSuccessful()) {
                        throw new IOException("Jarvis Core HTTP " + response.code());
                    }
                    return raw.isBlank() ? new JSONObject() : new JSONObject(raw);
                }
            } catch (Exception exception) {
                last = exception;
            }
        }
        throw last == null ? new IOException("Core offline") : last;
    }

    private List<String> endpoints() {
        Set<String> values = new LinkedHashSet<>();
        String configured = CoreEndpointSelector.normaliseBaseUrl(store.coreUrl());
        if (!configured.isBlank()) values.add(configured);
        String remote = CoreEndpointSelector.normaliseOptionalBaseUrl(store.remoteCoreUrl());
        if (!remote.isBlank()) values.add(remote);
        return new ArrayList<>(values);
    }

    static boolean isGoogleAuthorizationUrl(String value) {
        HttpUrl url = HttpUrl.parse(value);
        return url != null
            && url.isHttps()
            && "accounts.google.com".equals(url.host())
            && url.username().isEmpty()
            && url.password().isEmpty()
            && "/o/oauth2/v2/auth".equals(url.encodedPath())
            && "code".equals(url.queryParameter("response_type"))
            && url.queryParameter("client_id") != null
            && url.queryParameter("state") != null
            && url.queryParameter("code_challenge") != null;
    }

    private static String message(Exception exception) {
        String value = exception.getMessage();
        return value == null || value.isBlank() ? "Core offline" : value;
    }

    @Override public void close() {
        executor.shutdownNow();
    }
}
