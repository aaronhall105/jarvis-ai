package com.aaron.jarvisvoice;

import java.net.URI;

public final class CoreUrl {
    private CoreUrl() {}

    public static String validateBase(String baseUrl) throws Exception {
        URI base = URI.create(baseUrl == null ? "" : baseUrl.trim());
        String scheme = base.getScheme() == null ? "" : base.getScheme().toLowerCase();
        if (!(scheme.equals("http") || scheme.equals("https") || scheme.equals("ws") || scheme.equals("wss"))) {
            throw new IllegalArgumentException("Jarvis Core URL must start with http:// or https://");
        }
        if (base.getUserInfo() != null && !base.getUserInfo().isBlank()) {
            throw new IllegalArgumentException("Jarvis Core URL must not contain embedded credentials");
        }
        String host = base.getHost();
        if (host == null || host.isBlank()) {
            throw new IllegalArgumentException("Jarvis Core URL has no host");
        }
        if ((scheme.equals("http") || scheme.equals("ws")) && !isPrivateHost(host)) {
            throw new IllegalArgumentException(
                "Public Jarvis Core URLs must use HTTPS; cleartext is allowed only on the private LAN or Tailscale"
            );
        }
        return trimTrailingSlash(base.toString());
    }

    public static String websocket(String baseUrl) throws Exception {
        URI base = URI.create(validateBase(baseUrl));
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

    static boolean isPrivateHost(String rawHost) {
        String host = rawHost == null ? "" : rawHost.trim().toLowerCase();
        if (host.equals("localhost") || host.endsWith(".local") || host.equals("::1")) return true;
        if (host.startsWith("fc") || host.startsWith("fd")) return true;

        String[] parts = host.split("\\.");
        if (parts.length != 4) return false;
        int[] octets = new int[4];
        try {
            for (int index = 0; index < 4; index++) {
                octets[index] = Integer.parseInt(parts[index]);
                if (octets[index] < 0 || octets[index] > 255) return false;
            }
        } catch (NumberFormatException ignored) {
            return false;
        }

        int first = octets[0];
        int second = octets[1];
        if (first == 10 || first == 127) return true;
        if (first == 192 && second == 168) return true;
        if (first == 172 && second >= 16 && second <= 31) return true;
        // Tailscale uses the CGNAT range 100.64.0.0/10.
        return first == 100 && second >= 64 && second <= 127;
    }

    private static String trimTrailingSlash(String value) {
        String candidate = value == null ? "" : value.trim();
        while (candidate.endsWith("/")) {
            candidate = candidate.substring(0, candidate.length() - 1);
        }
        return candidate;
    }
}
