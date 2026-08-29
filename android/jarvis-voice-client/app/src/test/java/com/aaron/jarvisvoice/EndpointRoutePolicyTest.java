package com.aaron.jarvisvoice;

import static org.junit.Assert.assertEquals;

import java.util.List;

import org.junit.Test;

public class EndpointRoutePolicyTest {
    private static final String LAN =
        "http://192.168.1.40:8000";

    private static final String REMOTE =
        "https://arvis.tail7378d0.ts.net";

    @Test
    public void homePrefersLanThenSecureRemote() {
        assertEquals(
            List.of(LAN, REMOTE),
            EndpointRoutePolicy.order(
                true,
                LAN,
                REMOTE
            )
        );
    }

    @Test
    public void awayPrefersSecureRemoteThenLan() {
        assertEquals(
            List.of(REMOTE, LAN),
            EndpointRoutePolicy.order(
                false,
                LAN,
                REMOTE
            )
        );
    }

    @Test
    public void trailingSlashesCannotCreateDuplicateRoutes() {
        assertEquals(
            List.of(LAN),
            EndpointRoutePolicy.order(
                true,
                LAN + "/",
                LAN
            )
        );
    }

    @Test
    public void missingRemoteDoesNotInventInternetEndpoint() {
        assertEquals(
            List.of(LAN),
            EndpointRoutePolicy.order(
                false,
                LAN,
                ""
            )
        );
    }

    @Test
    public void developerAndCoreUseSameOrderingPolicy() {
        assertEquals(
            CoreEndpointSelector.preferenceOrder(
                false,
                LAN,
                REMOTE
            ),
            DeveloperEndpointPolicy.order(
                false,
                LAN,
                REMOTE
            )
        );
    }
}
