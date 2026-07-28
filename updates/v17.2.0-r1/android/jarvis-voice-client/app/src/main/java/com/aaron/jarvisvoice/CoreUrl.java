package com.aaron.jarvisvoice;

import java.net.URI;

public final class CoreUrl {
    private CoreUrl() {}

    public static String websocket(String baseUrl) throws Exception {
        URI base = URI.create(baseUrl == null ? "" : baseUrl.trim());
        String scheme;
        if ("https".equalsIgnoreCase(base.getScheme())) {
            scheme = "wss";
        } else if ("http".equalsIgnoreCase(base.getScheme())) {
            scheme = "ws";
        } else if ("wss".equalsIgnoreCase(base.getScheme()) || "ws".equalsIgnoreCase(base.getScheme())) {
            scheme = base.getScheme().toLowerCase();
        } else {
            throw new IllegalArgumentException("Jarvis Core URL must start with http:// or https://");
        }
        if (base.getHost() == null || base.getHost().isBlank()) {
            throw new IllegalArgumentException("Jarvis Core URL has no host");
        }
        String path = base.getPath() == null ? "" : base.getPath();
        while (path.endsWith("/")) path = path.substring(0, path.length() - 1);
        if (!path.endsWith("/api/realtime/voice")) path += "/api/realtime/voice";
        return new URI(
            scheme,
            base.getUserInfo(),
            base.getHost(),
            base.getPort(),
            path,
            null,
            null
        ).toString();
    }
}
