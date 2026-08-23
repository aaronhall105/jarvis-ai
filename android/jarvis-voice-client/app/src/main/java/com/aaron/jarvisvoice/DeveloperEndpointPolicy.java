package com.aaron.jarvisvoice;

import java.util.List;

/**
 * Compatibility facade for Developer Mode.
 *
 * All actual ordering decisions belong to EndpointRoutePolicy so
 * Developer and normal Jarvis cannot silently diverge.
 */
final class DeveloperEndpointPolicy {
    private DeveloperEndpointPolicy() {}

    static List<String> order(
        boolean localNetwork,
        String local,
        String remote
    ) {
        return EndpointRoutePolicy.order(
            localNetwork,
            local,
            remote
        );
    }
}
