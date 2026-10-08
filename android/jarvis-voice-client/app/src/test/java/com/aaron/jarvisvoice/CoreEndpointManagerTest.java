package com.aaron.jarvisvoice;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

import android.content.Context;
import android.content.SharedPreferences;

import org.junit.After;
import org.junit.Before;
import org.junit.Test;
import org.junit.runner.RunWith;
import org.robolectric.RobolectricTestRunner;
import org.robolectric.RuntimeEnvironment;
import org.robolectric.annotation.Config;

import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.atomic.AtomicReference;

@RunWith(RobolectricTestRunner.class)
@Config(sdk = 35)
public final class CoreEndpointManagerTest {
    private static final String LAN = "http://192.168.50.40:8000";
    private static final String REMOTE = "http://100.64.10.20:8000";
    private Context context;
    private SharedPreferences preferences;

    @Before public void setUp() {
        context = RuntimeEnvironment.getApplication();
        preferences = context.getSharedPreferences("jarvis_voice_settings", Context.MODE_PRIVATE);
        preferences.edit().clear()
            .putString("core_url", LAN)
            .putString("remote_core_url_v190140", REMOTE)
            .putBoolean("remote_core_migration_v190140", true)
            .putString("mobile_voice_token", "opaque-encrypted-token")
            .apply();
        CoreEndpointManager.resetForTests();
    }

    @After public void tearDown() {
        CoreEndpointManager.resetForTests();
        preferences.edit().clear().apply();
    }

    @Test public void lastKnownGoodLeadsOnlyWhenItIsStillConfigured() {
        assertEquals(
            List.of(REMOTE, LAN),
            CoreEndpointManager.orderedCandidates(true, LAN, REMOTE, "", REMOTE)
        );
        assertEquals(
            List.of(LAN, REMOTE),
            CoreEndpointManager.orderedCandidates(
                true, LAN, REMOTE, "", "http://192.168.50.99:8000"
            )
        );
    }

    @Test public void successfulAlternateBecomesSharedActiveAndPersistsWithoutAuthReset() {
        CoreEndpointManager manager = CoreEndpointManager.get(context);
        ArrayList<CoreEndpointManager.Snapshot> observed = new ArrayList<>();
        manager.addListener(observed::add);

        manager.reportTransportFailure(LAN);
        manager.reportSuccess(REMOTE);
        org.robolectric.Shadows.shadowOf(android.os.Looper.getMainLooper()).idle();

        assertEquals(REMOTE, manager.snapshot().endpoint());
        assertEquals(CoreEndpointManager.State.CONNECTED, manager.snapshot().state());
        assertEquals(REMOTE, new SecureStore(context).lastGoodCoreUrl());
        assertEquals(REMOTE, manager.candidates().get(0));
        assertEquals(REMOTE, CoreEndpointSelector.candidateUrls(context, LAN, REMOTE).get(0));
        assertEquals("opaque-encrypted-token", preferences.getString("mobile_voice_token", ""));
        assertTrue(observed.stream().anyMatch(
            value -> value.state() == CoreEndpointManager.State.CONNECTED
                && REMOTE.equals(value.endpoint())
        ));
    }

    @Test public void unconfiguredNetworkUrlCannotBecomeActiveOrLastKnownGood() {
        CoreEndpointManager manager = CoreEndpointManager.get(context);
        String arbitrary = "https://attacker.example";

        manager.reportSuccess(arbitrary);

        assertFalse(arbitrary.equals(manager.snapshot().endpoint()));
        assertFalse(manager.candidates().contains(arbitrary));
        assertFalse(arbitrary.equals(new SecureStore(context).lastGoodCoreUrl()));
    }

    @Test public void missingRemoteDoesNotInventTailscaleOrPublicEndpoint() {
        assertEquals(
            List.of(LAN),
            CoreEndpointManager.orderedCandidates(false, LAN, "", "", "")
        );
    }

    @Test public void failedEndpointCoolsDownAndAuthenticatedSuccessClearsIt() {
        CoreEndpointManager manager = CoreEndpointManager.get(context);

        manager.reportSuccess(LAN);
        manager.reportTransportFailure(LAN);

        assertTrue(manager.isCoolingDown(LAN));
        assertEquals(REMOTE, manager.candidates().get(0));
        manager.reportSuccess(LAN);
        assertFalse(manager.isCoolingDown(LAN));
    }

    @Test public void unavailableLocalFailsOverToCompatibleConfiguredCore() throws Exception {
        java.net.ServerSocket server = new java.net.ServerSocket(0, 1,
            java.net.InetAddress.getByName("127.0.0.1"));
        Thread responder = new Thread(() -> {
            try (java.net.Socket socket = server.accept()) {
                java.io.BufferedReader input = new java.io.BufferedReader(
                    new java.io.InputStreamReader(
                        socket.getInputStream(), java.nio.charset.StandardCharsets.US_ASCII
                    )
                );
                while (true) {
                    String line = input.readLine();
                    if (line == null || line.isBlank()) break;
                }
                byte[] body = ("{\"status\":\"healthy\",\"service\":\"Jarvis\","
                    + "\"version\":\"test\",\"source_commit\":\"fixture\"}")
                    .getBytes(java.nio.charset.StandardCharsets.UTF_8);
                java.io.OutputStream output = socket.getOutputStream();
                output.write(("HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
                    + "Content-Length: " + body.length + "\r\nConnection: close\r\n\r\n")
                    .getBytes(java.nio.charset.StandardCharsets.US_ASCII));
                output.write(body);
                output.flush();
            } catch (Exception ignored) {
            }
        });
        responder.start();
        try {
            String healthy = "http://127.0.0.1:" + server.getLocalPort();
            preferences.edit()
                .putString("core_url", "http://127.0.0.1:1")
                .putString("remote_core_url_v190140", healthy)
                .apply();
            CoreEndpointManager.resetForTests();
            CoreEndpointManager manager = CoreEndpointManager.get(context);
            AtomicReference<String> selected = new AtomicReference<>("");
            manager.select(new CoreEndpointManager.SelectionCallback() {
                @Override public void onSelected(String endpoint, String name) {
                    selected.set(endpoint);
                }
                @Override public void onUnavailable(String message) {
                    selected.set("unavailable");
                }
            });

            long deadline = System.currentTimeMillis() + 6_000L;
            while (!healthy.equals(selected.get()) && System.currentTimeMillis() < deadline) {
                org.robolectric.Shadows.shadowOf(android.os.Looper.getMainLooper()).idle();
                Thread.sleep(25L);
            }
            org.robolectric.Shadows.shadowOf(android.os.Looper.getMainLooper()).idle();

            assertEquals(healthy, selected.get());
            assertEquals(CoreEndpointManager.State.RECOVERING, manager.snapshot().state());
            assertEquals("", new SecureStore(context).lastGoodCoreUrl());
            manager.reportSuccess(healthy);
            assertEquals(healthy, new SecureStore(context).lastGoodCoreUrl());
        } finally {
            server.close();
            responder.join(1_000L);
        }
    }
}
