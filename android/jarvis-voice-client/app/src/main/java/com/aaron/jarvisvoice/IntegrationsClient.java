package com.aaron.jarvisvoice;

import android.content.Context;
import android.os.Handler;
import android.os.Looper;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.IOException;
import java.util.ArrayList;
import java.util.List;
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
    public enum FailureKind {
        AUTHENTICATION_REJECTED,
        SETUP_REQUIRED,
        PROVIDER_UNAVAILABLE,
        CORE_UNREACHABLE
    }

    public static final class Failure {
        public final FailureKind kind;
        public final String message;

        Failure(FailureKind kind, String message) {
            this.kind = kind;
            this.message = message;
        }
    }

    public interface ProvidersCallback {
        void onSuccess(List<IntegrationProvider> providers);
        void onError(Failure failure);
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
    static final long PROVIDER_READ_TIMEOUT_SECONDS = 45L;
    static final long PROVIDER_CALL_TIMEOUT_SECONDS = 50L;
    private final SecureStore store;
    private final Context context;
    private final Handler main = new Handler(Looper.getMainLooper());
    private final ExecutorService executor = Executors.newSingleThreadExecutor();
    private final OkHttpClient client = new OkHttpClient.Builder()
        .connectTimeout(5, TimeUnit.SECONDS)
        .readTimeout(PROVIDER_READ_TIMEOUT_SECONDS, TimeUnit.SECONDS)
        .callTimeout(PROVIDER_CALL_TIMEOUT_SECONDS, TimeUnit.SECONDS)
        .retryOnConnectionFailure(false)
        .build();

    public IntegrationsClient(Context context) {
        this.context = context.getApplicationContext();
        store = new SecureStore(this.context);
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
                main.post(() -> callback.onError(failure(exception)));
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
        String mobileToken = store.mobileToken();
        if (mobileToken.isBlank()) {
            throw new IntegrationException(new Failure(
                FailureKind.SETUP_REQUIRED,
                "Mobile voice token is not configured"
            ));
        }
        IOException lastTransportFailure = null;
        for (String endpoint : endpoints()) {
            try {
                HttpUrl url = HttpUrl.parse(endpoint + path);
                if (url == null) continue;
                Request.Builder request = new Request.Builder()
                    .url(url)
                    .header("Authorization", "Bearer " + mobileToken)
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
                    if (!response.isSuccessful()) {
                        throw new IntegrationException(failureForHttpCode(response.code()));
                    }
                    String raw = response.body() == null ? "" : response.body().string();
                    try {
                        return raw.isBlank() ? new JSONObject() : new JSONObject(raw);
                    } catch (Exception exception) {
                        throw new IntegrationException(new Failure(
                            FailureKind.PROVIDER_UNAVAILABLE,
                            "Jarvis Core returned a malformed integrations response"
                        ));
                    }
                }
            } catch (IntegrationException exception) {
                // Any HTTP response proves that this authoritative Core is reachable.
                // Do not hide a meaningful rejection or server state behind a later
                // transport failure from another candidate endpoint.
                throw exception;
            } catch (IOException exception) {
                lastTransportFailure = exception;
            }
        }
        throw new IntegrationException(new Failure(
            FailureKind.CORE_UNREACHABLE,
            lastTransportFailure == null
                ? "Jarvis Core could not be reached"
                : message(lastTransportFailure)
        ));
    }

    private List<String> endpoints() {
        return CoreEndpointSelector.candidateUrls(
            context,
            store.coreUrl(),
            store.remoteCoreUrl()
        );
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

    static Failure failureForHttpCode(int code) {
        if (code == 401 || code == 403) {
            return new Failure(
                FailureKind.AUTHENTICATION_REJECTED,
                "Jarvis Core rejected the mobile voice token"
            );
        }
        if (code == 503) {
            return new Failure(
                FailureKind.SETUP_REQUIRED,
                "Jarvis Core integrations setup is incomplete"
            );
        }
        return new Failure(
            FailureKind.PROVIDER_UNAVAILABLE,
            "Jarvis Core integrations request failed with HTTP " + code
        );
    }

    private static Failure failure(Exception exception) {
        if (exception instanceof IntegrationException integrationException) {
            return integrationException.failure;
        }
        return new Failure(FailureKind.CORE_UNREACHABLE, message(exception));
    }

    private static String message(Exception exception) {
        String value = exception.getMessage();
        return value == null || value.isBlank() ? "Core offline" : value;
    }

    private static final class IntegrationException extends IOException {
        final Failure failure;

        IntegrationException(Failure failure) {
            super(failure.message);
            this.failure = failure;
        }
    }

    @Override public void close() {
        executor.shutdownNow();
    }
}
