package com.aaron.jarvisvoice;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

import org.json.JSONArray;
import org.json.JSONObject;
import org.junit.Test;

public final class IntegrationProviderTest {
    @Test public void partialPermissionsRemainDistinctFromConnectedLabel() throws Exception {
        JSONObject value = new JSONObject()
            .put("provider_id", "google")
            .put("name", "Google")
            .put("state", "Partial permissions")
            .put("connected", true)
            .put("healthy", true)
            .put("granted_capabilities", new JSONArray().put("gmail.search"))
            .put("account", new JSONObject().put("account_id", "account-1"))
            .put("can_reconnect", true)
            .put("can_disconnect", true);

        IntegrationProvider provider = IntegrationProvider.fromJson(value);

        assertEquals("Partial permissions", provider.state);
        assertTrue(provider.connected);
        assertTrue(provider.healthy);
        assertEquals("account-1", provider.accountId);
        assertEquals(1, provider.grantedCapabilities.size());
    }

    @Test public void setupRequiredNeverBecomesConnected() throws Exception {
        JSONObject value = new JSONObject()
            .put("provider_id", "microsoft")
            .put("name", "Microsoft")
            .put("state", "Setup required")
            .put("connected", false)
            .put("healthy", false)
            .put("setup_requirements", new JSONArray().put("Connector unavailable"));

        IntegrationProvider provider = IntegrationProvider.fromJson(value);

        assertFalse(provider.connected);
        assertFalse(provider.healthy);
        assertEquals("Connector unavailable", provider.detail());
    }

    @Test public void googleOAuthUrlRequiresExactGoogleOriginAndPkceParameters() {
        String valid = "https://accounts.google.com/o/oauth2/v2/auth"
            + "?response_type=code&client_id=client&state=state&code_challenge=challenge";

        assertTrue(IntegrationsClient.isGoogleAuthorizationUrl(valid));
        assertFalse(IntegrationsClient.isGoogleAuthorizationUrl(
            "https://accounts.google.com.evil.test/o/oauth2/v2/auth"
                + "?response_type=code&client_id=x&state=x&code_challenge=x"
        ));
        assertFalse(IntegrationsClient.isGoogleAuthorizationUrl(
            "https://accounts.google.com/o/oauth2/v2/auth?response_type=code&client_id=x"
        ));
    }
}
