package com.aaron.jarvisvoice;

import android.content.Context;
import android.os.Handler;
import android.os.Looper;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.IOException;
import java.util.ArrayList;
import java.util.List;
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

/** Principal-scoped mobile client for the unified Task Centre API. */
public final class TaskCentreClient implements AutoCloseable {
    private static final class TaskRequestException extends Exception {
        TaskRequestException(String message) { super(message); }
    }
    public interface ListCallback {
        void onSuccess(List<TaskItem> tasks, JSONObject counts);
        void onError(String message);
    }

    public interface TaskCallback {
        void onSuccess(TaskItem task);
        void onError(String message);
    }

    public record CachedList(List<TaskItem> tasks, JSONObject counts, long receivedAtMillis) {}

    private static final MediaType JSON = MediaType.get("application/json; charset=utf-8");
    private final Context context;
    private final SecureStore store;
    private final CoreEndpointManager endpoints;
    private final TaskSnapshotStore snapshots;
    private final Handler main = new Handler(Looper.getMainLooper());
    private final ExecutorService executor = Executors.newSingleThreadExecutor();
    private final OkHttpClient client = new OkHttpClient.Builder()
        .connectTimeout(5, TimeUnit.SECONDS)
        .readTimeout(30, TimeUnit.SECONDS)
        .callTimeout(35, TimeUnit.SECONDS)
        .retryOnConnectionFailure(false)
        .build();

    public TaskCentreClient(Context context) {
        this.context = context.getApplicationContext();
        store = new SecureStore(this.context);
        endpoints = CoreEndpointManager.get(this.context);
        snapshots = new TaskSnapshotStore(this.context, store.userId());
    }

    public void list(String filter, ListCallback callback) {
        executor.execute(() -> {
            try {
                JSONObject response = request(
                    "GET", List.of("api", "tasks"),
                    new JSONObject(), "filter", filter == null ? "ACTIVE" : filter
                );
                List<TaskItem> tasks = parseTasks(response);
                JSONObject counts = response.optJSONObject("counts");
                snapshots.save(filter, response, System.currentTimeMillis());
                main.post(() -> callback.onSuccess(
                    tasks, counts == null ? new JSONObject() : counts
                ));
            } catch (Exception exception) {
                main.post(() -> callback.onError(userMessage(exception)));
            }
        });
    }

    public CachedList cached(String filter) {
        TaskSnapshotStore.Snapshot snapshot = snapshots.load(filter);
        if (snapshot == null) return null;
        JSONObject counts = snapshot.response().optJSONObject("counts");
        return new CachedList(
            parseTasks(snapshot.response()),
            counts == null ? new JSONObject() : counts,
            snapshot.receivedAtMillis()
        );
    }

    public TaskItem cachedTask(String taskId) {
        for (String filter : List.of(
            "ACTIVE", "WAITING_FOR_YOU", "SCHEDULED", "COMPLETED", "PROBLEMS"
        )) {
            CachedList cached = cached(filter);
            if (cached == null) continue;
            for (TaskItem task : cached.tasks()) {
                if (taskId.equals(task.taskId)) return task;
            }
        }
        return null;
    }

    public void task(String taskId, TaskCallback callback) {
        call("GET", taskId, null, new JSONObject(), callback);
    }

    public void action(String taskId, String action, TaskCallback callback) {
        JSONObject payload = new JSONObject();
        try { payload.put("request_id", UUID.randomUUID().toString()); }
        catch (Exception ignored) { }
        call("POST", taskId, action, payload, callback);
    }

    public void notifications(
        String taskId,
        boolean completion,
        boolean failure,
        TaskCallback callback
    ) {
        JSONObject payload = new JSONObject();
        try {
            payload.put("notify_on_completion", completion);
            payload.put("notify_on_failure", failure);
        } catch (Exception ignored) { }
        call("POST", taskId, "notifications", payload, callback);
    }

    public void steer(String taskId, String instruction, TaskCallback callback) {
        JSONObject payload = new JSONObject();
        try { payload.put("instruction", instruction); }
        catch (Exception ignored) { }
        call("POST", taskId, "steer", payload, callback);
    }

    public void reschedule(
        String taskId,
        String dueAt,
        String timezone,
        TaskCallback callback
    ) {
        JSONObject payload = new JSONObject();
        try {
            payload.put("request_id", UUID.randomUUID().toString());
            payload.put("due_at", dueAt);
            payload.put("timezone", timezone);
        } catch (Exception ignored) { }
        call("POST", taskId, "reschedule", payload, callback);
    }

    private void call(
        String method,
        String taskId,
        String action,
        JSONObject payload,
        TaskCallback callback
    ) {
        executor.execute(() -> {
            try {
                List<String> segments = new ArrayList<>(List.of("api", "tasks", taskId));
                if (action != null && !action.isBlank()) segments.add(action);
                JSONObject response = request(method, segments, payload, null, null);
                TaskItem task = TaskItem.fromJson(response);
                main.post(() -> callback.onSuccess(task));
            } catch (Exception exception) {
                main.post(() -> callback.onError(userMessage(exception)));
            }
        });
    }

    private JSONObject request(
        String method,
        List<String> path,
        JSONObject payload,
        String queryName,
        String queryValue
    ) throws Exception {
        String token = store.mobileToken();
        if (token.isBlank()) throw new IOException("Mobile voice token is not configured");
        IOException last = null;
        for (String endpoint : endpoints.candidates()) {
            try {
                HttpUrl base = HttpUrl.parse(endpoint);
                if (base == null) continue;
                HttpUrl.Builder url = base.newBuilder();
                for (String segment : path) url.addPathSegment(segment);
                if (queryName != null && queryValue != null) {
                    url.addQueryParameter(queryName, queryValue);
                }
                Request.Builder request = new Request.Builder()
                    .url(url.build())
                    .header("Authorization", "Bearer " + token)
                    .header("Accept", "application/json");
                if ("GET".equals(method)) request.get();
                else request.post(RequestBody.create(payload.toString(), JSON));
                try (Response response = client.newCall(request.build()).execute()) {
                    String raw = response.body() == null ? "" : response.body().string();
                    if (!response.isSuccessful()) {
                        String detail = raw;
                        try { detail = new JSONObject(raw).optString("detail", raw); }
                        catch (Exception ignored) { }
                        throw new TaskRequestException(
                            detail.isBlank() ? "Task request failed" : detail
                        );
                    }
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

    static String userMessage(Exception exception) {
        if (exception instanceof TaskRequestException) {
            String value = exception.getMessage();
            return value == null || value.isBlank() ? "Task request failed." : value;
        }
        return "Can't reach Jarvis Core.";
    }

    private static List<TaskItem> parseTasks(JSONObject response) {
        List<TaskItem> tasks = new ArrayList<>();
        JSONArray values = response.optJSONArray("tasks");
        if (values == null) return tasks;
        for (int index = 0; index < values.length(); index++) {
            JSONObject value = values.optJSONObject(index);
            if (value != null) tasks.add(TaskItem.fromJson(value));
        }
        return tasks;
    }

    @Override public void close() {
        executor.shutdownNow();
    }
}
