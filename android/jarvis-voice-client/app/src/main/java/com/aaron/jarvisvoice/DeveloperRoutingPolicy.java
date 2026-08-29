package com.aaron.jarvisvoice;

final class DeveloperRoutingPolicy {
    private DeveloperRoutingPolicy() {}

    static boolean routesToDeveloper(AssistantMode mode) {
        return mode == AssistantMode.DEVELOPER;
    }

    static String placeholder(AssistantMode mode) {
        return routesToDeveloper(mode) ? "Message Developer" : "Message Jarvis";
    }
}
