package com.aaron.jarvisvoice;

/**
 * Prevents callbacks from an obsolete microphone or recogniser
 * generation from mutating a replacement capture session.
 */
final class CaptureEpochPolicy {
    private CaptureEpochPolicy() {}

    static boolean mayPublish(
        boolean running,
        int operationGeneration,
        int activeGeneration
    ) {
        return running
            && operationGeneration == activeGeneration;
    }
}
