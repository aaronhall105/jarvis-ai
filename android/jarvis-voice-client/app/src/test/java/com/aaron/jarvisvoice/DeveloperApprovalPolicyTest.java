package com.aaron.jarvisvoice;

import static org.junit.Assert.assertEquals;

import org.junit.Test;

public class DeveloperApprovalPolicyTest {
    @Test public void approvalAppliesToMatchingOperationsForTheSession() {
        assertEquals("Allow for session", DeveloperApprovalPolicy.primaryLabel());
        assertEquals("acceptForSession", DeveloperApprovalPolicy.approvalDecision());
    }
}
