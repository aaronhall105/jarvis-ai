package com.aaron.jarvisvoice;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.IOException;

import okhttp3.Call;
import okhttp3.Callback;
import okhttp3.MediaType;
import okhttp3.OkHttpClient;
import okhttp3.Request;
import okhttp3.RequestBody;
import okhttp3.Response;

public final class ImprovementApiClient {
    private static final MediaType JSON =
        MediaType.get("application/json; charset=utf-8");

    public interface CandidatesCallback {
        void onSuccess(JSONArray items);
        void onError(String message);
    }

    public interface JsonCallback {
        void onSuccess(JSONObject result);
        void onError(String message);
    }

    private final SecureStore store;
    private final OkHttpClient client;

    public ImprovementApiClient(SecureStore store) {
        this.store = store;
        this.client = new OkHttpClient();
    }

    public void loadCandidates(int limit, CandidatesCallback callback) {
        loadList(
            "/api/improvement/candidates?limit=" + clamp(limit),
            callback
        );
    }

    public void loadArchive(int limit, CandidatesCallback callback) {
        loadList(
            "/api/improvement/archive?limit=" + clamp(limit),
            callback
        );
    }

    public void loadCandidate(int candidateId, JsonCallback callback) {
        get("/api/improvement/candidates/" + candidateId, callback);
    }

    public void requestImprovement(String text, JsonCallback callback) {
        JSONObject body = new JSONObject();

        try {
            body.put("request", text);
        } catch (Exception exception) {
            callback.onError(safeMessage(exception));
            return;
        }

        post("/api/improvement/request", body, callback);
    }

    public void retry(int candidateId, JsonCallback callback) {
        post(
            "/api/improvement/candidates/"
                + candidateId
                + "/retry",
            new JSONObject(),
            callback
        );
    }

    public void approve(int candidateId, String code, JsonCallback callback) {
        actionWithCode(candidateId, "approve", code, callback);
    }

    public void deploy(int candidateId, String code, JsonCallback callback) {
        actionWithCode(candidateId, "deploy", code, callback);
    }

    public void reject(int candidateId, JsonCallback callback) {
        post(
            "/api/improvement/candidates/" + candidateId + "/reject",
            new JSONObject(),
            callback
        );
    }

    public void issueRollbackTicket(int candidateId, JsonCallback callback) {
        post(
            "/api/improvement/candidates/" + candidateId + "/rollback-ticket",
            new JSONObject(),
            callback
        );
    }

    public void rollback(int candidateId, String code, JsonCallback callback) {
        actionWithCode(candidateId, "rollback", code, callback);
    }

    public void archive(int candidateId, JsonCallback callback) {
        post(
            "/api/improvement/candidates/" + candidateId + "/archive",
            new JSONObject(),
            callback
        );
    }

    public void restore(int candidateId, JsonCallback callback) {
        post(
            "/api/improvement/candidates/" + candidateId + "/restore",
            new JSONObject(),
            callback
        );
    }

    private void actionWithCode(
        int candidateId,
        String action,
        String code,
        JsonCallback callback
    ) {
        JSONObject body = new JSONObject();

        try {
            body.put("code", code);
        } catch (Exception exception) {
            callback.onError(safeMessage(exception));
            return;
        }

        post(
            "/api/improvement/candidates/"
                + candidateId
                + "/"
                + action,
            body,
            callback
        );
    }

    private void loadList(
        String path,
        CandidatesCallback callback
    ) {
        get(
            path,
            new JsonCallback() {
                @Override
                public void onSuccess(JSONObject root) {
                    JSONArray items = root.optJSONArray("items");
                    callback.onSuccess(
                        items == null ? new JSONArray() : items
                    );
                }

                @Override
                public void onError(String message) {
                    callback.onError(message);
                }
            }
        );
    }

    private void get(String path, JsonCallback callback) {
        try {
            Request request = baseRequest(path)
                .get()
                .build();

            execute(request, callback);
        } catch (Exception exception) {
            callback.onError(safeMessage(exception));
        }
    }

    private void post(
        String path,
        JSONObject body,
        JsonCallback callback
    ) {
        try {
            RequestBody requestBody =
                RequestBody.create(body.toString(), JSON);

            Request request = baseRequest(path)
                .post(requestBody)
                .build();

            execute(request, callback);
        } catch (Exception exception) {
            callback.onError(safeMessage(exception));
        }
    }

    private Request.Builder baseRequest(String path) {
        String token = store.improvementAdminToken();

        if (token.isBlank()) {
            throw new IllegalStateException(
                "Improvement administrator access is not configured."
            );
        }

        String base = store.coreUrl();

        while (base.endsWith("/")) {
            base = base.substring(0, base.length() - 1);
        }

        return new Request.Builder()
            .url(base + path)
            .header("X-Jarvis-Admin-Token", token);
    }

    private void execute(
        Request request,
        JsonCallback callback
    ) {
        client.newCall(request).enqueue(
            new Callback() {
                @Override
                public void onFailure(
                    Call call,
                    IOException exception
                ) {
                    callback.onError(safeMessage(exception));
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
                                errorMessage(response.code(), body)
                            );
                            return;
                        }

                        callback.onSuccess(
                            body.isBlank()
                                ? new JSONObject()
                                : new JSONObject(body)
                        );
                    } catch (Exception exception) {
                        callback.onError(safeMessage(exception));
                    }
                }
            }
        );
    }

    private static String errorMessage(
        int code,
        String body
    ) {
        try {
            JSONObject json = new JSONObject(body);
            String detail = json.optString("detail", "");

            if (!detail.isBlank() && !"null".equalsIgnoreCase(detail)) {
                return detail;
            }
        } catch (Exception ignored) {
        }

        return "Jarvis Core returned HTTP " + code;
    }

    private static int clamp(int limit) {
        return Math.max(1, Math.min(limit, 100));
    }

    private static String safeMessage(Exception exception) {
        String message = exception.getMessage();

        return message == null || message.isBlank()
            ? exception.getClass().getSimpleName()
            : message;
    }
}
