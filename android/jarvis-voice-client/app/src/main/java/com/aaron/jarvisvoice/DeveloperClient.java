package com.aaron.jarvisvoice;

import android.os.Handler;
import android.os.Looper;
import android.content.Context;
import org.json.JSONObject;
import org.json.JSONArray;
import java.util.concurrent.TimeUnit;
import java.util.List;
import java.util.HashMap;
import java.util.Map;
import okhttp3.OkHttpClient;
import okhttp3.Request;
import okhttp3.Response;
import okhttp3.WebSocket;
import okhttp3.WebSocketListener;

final class DeveloperClient {
    interface Listener {
        void onState(String state);
        void onEvent(JSONObject event);
        void onError(String message);
    }

    private final Handler main = new Handler(Looper.getMainLooper());
    private final OkHttpClient http = new OkHttpClient.Builder()
        .pingInterval(20, TimeUnit.SECONDS).retryOnConnectionFailure(true).build();
    private final Listener listener;
    private WebSocket socket;
    private String token = "";
    private String workspace = "jarvis-wear";
    private String threadId = "";
    private String activeTurnId = "";
    private long requestId;
    private final NetworkQualityMonitor network;
    private List<String> endpoints = List.of();
    private int endpointIndex;
    private long connectionEpoch;
    private final Map<Long, String> requestKinds = new HashMap<>();
    private String localUrl = "";
    private String remoteUrl = "";
    private boolean enabled;
    private boolean authenticated;
    private boolean sessionReady;
    private long reconnectDelayMs = 1000;
    private boolean reconnectScheduled;
    private boolean lastLocalNetwork;

    DeveloperClient(Context context, Listener listener) {
        this.listener = listener;

        network = new NetworkQualityMonitor(
            context,
            new NetworkQualityMonitor.Listener() {
                @Override public void onNetworkAvailable() {
                    main.post(
                        DeveloperClient.this::networkChanged
                    );
                }

                @Override public void onNetworkLost() {
                    main.post(
                        DeveloperClient.this::networkChanged
                    );
                }
            }
        );
    }

    void connect(String localUrl, String remoteUrl, String token, String workspace, String restoredThread) {
        closeSocket();
        enabled = true;
        this.localUrl = localUrl;
        this.remoteUrl = remoteUrl;
        this.token = token;
        this.workspace = workspace;
        this.threadId = restoredThread == null ? "" : restoredThread;
        lastLocalNetwork = hasLocalNetwork();
        endpoints = DeveloperEndpointPolicy.order(lastLocalNetwork, localUrl, remoteUrl);
        endpointIndex = 0;
        connectCurrent();
    }

    private void connectCurrent() {
        if (!enabled) return;

        if (!network.isAvailable()) {
            postState("Offline — waiting for network");
            return;
        }

        if (endpoints.isEmpty()) {
            postError("Developer endpoint is not configured");
            return;
        }
        String ws = endpoints.get(endpointIndex).replaceFirst("^http://", "ws://").replaceFirst("^https://", "wss://")
            + "/api/developer";
        listener.onState("Connecting…");
        long epoch = ++connectionEpoch;
        WebSocket created = http.newWebSocket(new Request.Builder().url(ws).build(), new WebSocketListener() {
            @Override public void onOpen(WebSocket webSocket, Response response) {
                if (!isCurrent(webSocket, epoch)) return;
                webSocket.send(json("type", "auth", "token", DeveloperClient.this.token).toString());
            }
            @Override public void onMessage(WebSocket webSocket, String text) {
                if (!isCurrent(webSocket, epoch)) return;
                try { handle(new JSONObject(text)); }
                catch (Exception exception) { postError("Invalid Developer response"); }
            }
            @Override public void onFailure(WebSocket webSocket, Throwable failure, Response response) {
                if (!isCurrent(webSocket, epoch)) return;
                if (endpointIndex + 1 < endpoints.size()) {
                    endpointIndex++;
                    main.postDelayed(() -> {
                        if (!enabled || connectionEpoch != epoch) return;
                        listener.onState("Trying secure remote connection…"); connectCurrent();
                    }, 400);
                } else scheduleReconnect("Developer PC unavailable");
            }
            @Override public void onClosed(WebSocket webSocket, int code, String reason) {
                if (isCurrent(webSocket, epoch) && enabled) scheduleReconnect("Developer connection closed");
            }
        });
        socket = created;
    }

    private boolean isCurrent(WebSocket candidate, long epoch) {
        return socket == candidate && connectionEpoch == epoch;
    }

    private boolean hasLocalNetwork() {
        return network.isLocalTransport();
    }

    void sendInstruction(String text) { sendInstruction(text, new JSONArray()); }
    void sendInstruction(String text, JSONArray attachments) {
        if (!authenticated || !sessionReady) {
            if (!pendingInstruction.isBlank()) { postError("A Developer message is already queued"); return; }
            pendingInstruction = text;
            pendingAttachments = attachments == null ? new JSONArray() : attachments;
            postState("Queued — reconnecting…");
            return;
        }
        if (threadId.isBlank()) {
            long id = nextRequest("thread.start");
            send(json("type", "thread.start", "workspace", workspace, "request_id", id));
            pendingInstruction = text;
            pendingAttachments = attachments == null ? new JSONArray() : attachments;
        } else sendTurn(text, attachments);
    }

    private String pendingInstruction = "";
    private JSONArray pendingAttachments = new JSONArray();
    private void sendTurn(String text, JSONArray attachments) {
        long id = nextRequest("turn.start");
        send(json("type", "turn.start", "workspace", workspace,
            "thread_id", threadId, "text", text, "attachments",
            attachments == null ? new JSONArray() : attachments, "request_id", id));
    }

    private void handle(JSONObject message) {
        String type = message.optString("type");
        if ("auth.ok".equals(type)) {
            reconnectDelayMs = 1000;
            authenticated = true;
            if (threadId.isBlank()) { sessionReady = true; postState("Connected"); drainPendingInstruction(); }
            else {
                sessionReady = false;
                postState("Restoring session…");
                long id = nextRequest("thread.resume");
                send(json("type", "thread.resume", "workspace", workspace,
                    "thread_id", threadId, "request_id", id));
            }
            refreshThreads();
            requestRateLimits();
        }
        else if ("response".equals(type)) {
            long responseId = message.optLong("request_id", -1);
            String requestKind = requestKinds.remove(responseId);
            if (requestKind != null) {
                try { message.put("request_kind", requestKind); }
                catch (Exception ignored) { }
            }
            JSONObject result = message.optJSONObject("result");
            JSONObject thread = result == null ? null : result.optJSONObject("thread");
            if (thread != null && threadId.isBlank()) {
                threadId = thread.optString("id");
                if (!pendingInstruction.isBlank()) {
                    String text = pendingInstruction; JSONArray attachments = pendingAttachments;
                    pendingInstruction = ""; pendingAttachments = new JSONArray();
                    sendTurn(text, attachments);
                }
            }
            if ("thread.resume".equals(requestKind)) { sessionReady = true; postState("Connected"); drainPendingInstruction(); }
            postEvent(message);
        } else if ("codex.event".equals(type)) {
            JSONObject event = message.optJSONObject("event");
            if (event != null && "turn/started".equals(event.optString("method"))) {
                JSONObject params = event.optJSONObject("params");
                JSONObject turn = params == null ? null : params.optJSONObject("turn");
                activeTurnId = turn == null ? "" : turn.optString("id");
            } else if (event != null && "turn/completed".equals(event.optString("method"))) {
                activeTurnId = "";
                refreshThreads();
                requestRateLimits();
            }
            postEvent(event);
        }
        else if ("error".equals(type) || "auth.error".equals(type)) postError(message.optString("message", "Developer authentication failed"));
    }

    String threadId() { return threadId; }
    void listThreads() {
        long id = nextRequest("threads.list");
        send(json("type", "threads.list", "workspace", workspace, "request_id", id));
    }
    void refreshThreads() {
        long id = nextRequest("threads.refresh");
        send(json("type", "threads.refresh", "workspace", workspace, "request_id", id));
    }
    void requestRateLimits() {
        long id = nextRequest("account.rate_limits");
        send(json("type", "account.rate_limits", "request_id", id));
    }
    void selectThread(String selectedThreadId) {
        threadId = selectedThreadId == null ? "" : selectedThreadId;
        activeTurnId = "";
        long id = nextRequest("thread.resume");
        send(json("type", "thread.resume", "workspace", workspace,
            "thread_id", threadId, "request_id", id));
        postState("Restoring session…");
    }
    void newSession() {
        threadId = "";
        activeTurnId = "";
        pendingInstruction = "";
        pendingAttachments = new JSONArray();
        requestKinds.clear();
    }
    void deleteCurrentThread() {
        if (!authenticated || threadId.isBlank()) return;
        long id = nextRequest("thread.delete");
        send(json("type", "thread.delete", "thread_id", threadId, "request_id", id));
    }
    void respondToApproval(long codexRequestId, String decision) {
        long id = nextRequest("approval.respond");
        send(json("type", "approval.respond", "codex_request_id", codexRequestId,
            "decision", decision, "request_id", id));
    }
    void interrupt() {
        if (socket == null || threadId.isBlank() || activeTurnId.isBlank()) return;
        long id = nextRequest("turn.interrupt");
        send(json("type", "turn.interrupt", "thread_id", threadId,
            "turn_id", activeTurnId, "request_id", id));
    }
    void close() {
        enabled = false;
        main.removeCallbacksAndMessages(reconnectToken);
        reconnectScheduled = false;
        closeSocket();
    }
    void destroy() {
        close();
        network.close();
        http.dispatcher().executorService().shutdown();
    }
    private void closeSocket() {
        connectionEpoch++;
        if (socket != null) socket.close(1000, "client closing");
        socket = null;
        authenticated = false;
        sessionReady = false;
        activeTurnId = "";
        requestKinds.clear();
    }
    private final Object reconnectToken = new Object();
    private void scheduleReconnect(String reason) {
        if (!enabled || reconnectScheduled) return;

        if (!network.isAvailable()) {
            postState("Offline — waiting for network");
            return;
        }

        reconnectScheduled = true;
        postError(reason);
        long delay = reconnectDelayMs;
        reconnectDelayMs = Math.min(30_000, reconnectDelayMs * 2);
        main.postAtTime(() -> {
            reconnectScheduled = false;
            if (!enabled) return;
            closeSocket();
            lastLocalNetwork = hasLocalNetwork();
            endpoints = DeveloperEndpointPolicy.order(lastLocalNetwork, localUrl, remoteUrl);
            endpointIndex = 0;
            connectCurrent();
        }, reconnectToken, android.os.SystemClock.uptimeMillis() + delay);
    }
    private void networkChanged() {
        main.post(() -> {
            if (!enabled) return;

            main.removeCallbacksAndMessages(reconnectToken);
            reconnectScheduled = false;
            reconnectDelayMs = 1000;

            if (!network.isAvailable()) {
                lastLocalNetwork = false;
                closeSocket();
                postState("Offline — waiting for network");
                return;
            }

            boolean localNetwork = hasLocalNetwork();

            if (
                socket != null
                    && localNetwork == lastLocalNetwork
            ) {
                return;
            }

            lastLocalNetwork = localNetwork;
            closeSocket();

            endpoints = DeveloperEndpointPolicy.order(
                lastLocalNetwork,
                localUrl,
                remoteUrl
            );
            endpointIndex = 0;

            connectCurrent();
        });
    }
    private long nextRequest(String kind) {
        long id = ++requestId;
        requestKinds.put(id, kind);
        return id;
    }
    private void drainPendingInstruction() {
        if (!sessionReady || pendingInstruction.isBlank()) return;
        String text = pendingInstruction; JSONArray attachments = pendingAttachments;
        pendingInstruction = ""; pendingAttachments = new JSONArray();
        if (threadId.isBlank()) {
            long id = nextRequest("thread.start");
            send(json("type", "thread.start", "workspace", workspace, "request_id", id));
            pendingInstruction = text; pendingAttachments = attachments;
        } else sendTurn(text, attachments);
    }
    private void send(JSONObject value) { WebSocket active = socket; if (active != null) active.send(value.toString()); }
    private static JSONObject json(Object... values) {
        JSONObject object = new JSONObject();
        try { for (int index = 0; index + 1 < values.length; index += 2) object.put(String.valueOf(values[index]), values[index + 1]); }
        catch (Exception ignored) {}
        return object;
    }
    private void postState(String value) { main.post(() -> listener.onState(value)); }
    private void postEvent(JSONObject value) { if (value != null) main.post(() -> listener.onEvent(value)); }
    private void postError(String value) { main.post(() -> { listener.onState("Reconnecting…"); listener.onError(value); }); }
}
