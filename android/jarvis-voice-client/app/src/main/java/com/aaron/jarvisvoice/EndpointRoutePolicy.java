package com.aaron.jarvisvoice;

import java.util.ArrayList;
import java.util.List;

/**
 * Authoritative routing policy shared by every Jarvis Android transport.
 *
 * Home/local transport:
 *     LAN -> secure remote fallback
 *
 * Away/non-local transport:
 *     secure remote -> LAN recovery fallback
 *
 * This class deliberately contains no Android networking code so the
 * policy remains deterministic and fully unit-testable.
 */
final class EndpointRoutePolicy {
    private EndpointRoutePolicy() {}

    static List<String> order(
        boolean localTransport,
        String local,
        String remote
    ) {
        String localValue = normalise(local);
        String remoteValue = normalise(remote);

        ArrayList<String> values = new ArrayList<>(2);

        if (localTransport) {
            append(values, localValue);
            append(values, remoteValue);
        } else {
            append(values, remoteValue);
            append(values, localValue);
        }

        return List.copyOf(values);
    }

    static String normalise(String value) {
        String candidate = value == null ? "" : value.trim();

        while (
            candidate.length() > 1
                && candidate.endsWith("/")
        ) {
            candidate = candidate.substring(
                0,
                candidate.length() - 1
            );
        }

        return candidate;
    }

    private static void append(
        ArrayList<String> values,
        String endpoint
    ) {
        if (
            endpoint.isBlank()
                || values.contains(endpoint)
        ) {
            return;
        }

        values.add(endpoint);
    }
}
