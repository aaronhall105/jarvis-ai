package com.aaron.jarvisvoice;

public final class TranscriptPolicyStandaloneTest {
    private static int count;

    private static void expect(TranscriptPolicy.Action expected, String text, boolean follow, boolean speaking) {
        TranscriptPolicy.Decision result = TranscriptPolicy.evaluate(text, "The bedroom and hallway lights are on", follow, speaking, "jarvis");
        if (result.action != expected) throw new AssertionError(text + " -> " + result.action + " expected " + expected);
        count++;
    }

    public static void main(String[] args) {
        expect(TranscriptPolicy.Action.COMMAND, "Jarvis turn the light off", false, false);
        expect(TranscriptPolicy.Action.COMMAND, "Hey Jarvis open YouTube", false, false);
        expect(TranscriptPolicy.Action.IGNORE, "turn the light off", false, false);
        expect(TranscriptPolicy.Action.COMMAND, "the bedroom", true, false);
        expect(TranscriptPolicy.Action.IGNORE, "turn it off", false, true);
        expect(TranscriptPolicy.Action.STOP, "Jarvis stop", false, true);
        expect(TranscriptPolicy.Action.STOP, "Jarvis be quiet", false, true);
        expect(TranscriptPolicy.Action.COMMAND, "Jarvis turn both lights off", false, true);
        expect(TranscriptPolicy.Action.IGNORE, "The bedroom and hallway lights are on", false, true);
        expect(TranscriptPolicy.Action.IGNORE, "Jarvis", false, false);
        expect(TranscriptPolicy.Action.STOP, "never mind", true, false);
        expect(TranscriptPolicy.Action.COMMAND, "yes", true, false);
        System.out.println("Passed " + count + " TranscriptPolicy tests");
    }
}
