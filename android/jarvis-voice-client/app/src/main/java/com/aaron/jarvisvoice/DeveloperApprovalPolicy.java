package com.aaron.jarvisvoice;

final class DeveloperApprovalPolicy {
    private DeveloperApprovalPolicy() { }

    static String primaryLabel() {
        return "Allow for session";
    }

    static String approvalDecision() {
        return "acceptForSession";
    }
}
