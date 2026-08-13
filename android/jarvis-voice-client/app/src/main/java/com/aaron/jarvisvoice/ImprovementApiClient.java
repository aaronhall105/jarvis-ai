package com.aaron.jarvisvoice;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.IOException;

import okhttp3.Call;
import okhttp3.Callback;
import okhttp3.OkHttpClient;
import okhttp3.Request;
import okhttp3.Response;

public final class ImprovementApiClient {
    public interface CandidatesCallback {
        void onSuccess(JSONArray items);
        void onError(String message);
    }

    private final SecureStore store;
    private final OkHttpClient client;

    public ImprovementApiClient(SecureStore store) {
        this.store = store;
        this.client = new OkHttpClient();
    }

    public void loadCandidates(
        int limit,
        CandidatesCallback callback
    ) {
        String token = store.improvementAdminToken();

        if (token.isBlank()) {
            callback.onError(
                "Improvement administrator access is not configured."
            );
            return;
        }

        String base = store.coreUrl();

        if (base.endsWith("/")) {
            base = base.substring(0, base.length() - 1);
        }

        String url =
            base
                + "/api/improvement/candidates?limit="
                + Math.max(1, Math.min(limit, 100));

        Request request = new Request.Builder()
            .url(url)
            .header(
                "X-Jarvis-Admin-Token",
                token
            )
            .get()
            .build();

        client.newCall(request).enqueue(
            new Callback() {
                @Override
                public void onFailure(
                    Call call,
                    IOException exception
                ) {
                    callback.onError(
                        safeMessage(exception)
                    );
                }

                @Override
                public void onResponse(
                    Call call,
                    Response response
                ) {
                    try (response) {
                        String body =
                            response.body() == null
                                ? ""
                                : response.body().string();

                        if (!response.isSuccessful()) {
                            callback.onError(
                                "Jarvis Core returned HTTP "
                                    + response.code()
                            );
                            return;
                        }

                        JSONObject root =
                            new JSONObject(body);

                        callback.onSuccess(
                            root.optJSONArray("items")
                                == null
                                ? new JSONArray()
                                : root.optJSONArray("items")
                        );
                    } catch (Exception exception) {
                        callback.onError(
                            safeMessage(exception)
                        );
                    }
                }
            }
        );
    }

    private static String safeMessage(
        Exception exception
    ) {
        String message = exception.getMessage();

        if (message == null || message.isBlank()) {
            return exception
                .getClass()
                .getSimpleName();
        }

        return message;
    }
}
