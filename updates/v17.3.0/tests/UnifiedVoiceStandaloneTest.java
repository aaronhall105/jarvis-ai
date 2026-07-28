import com.aaron.jarvisvoice.AudioFrameSizer;
import com.aaron.jarvisvoice.CoreUrl;
import com.aaron.jarvisvoice.VoiceCatalog;
import com.aaron.jarvisvoice.WakePhrasePolicy;

public final class UnifiedVoiceStandaloneTest {
    public static void main(String[] args) throws Exception {
        assertEquals(960, AudioFrameSizer.bytesFor(24_000, 20));
        assertEquals(
            "ws://192.168.1.40:8000/api/realtime/voice",
            CoreUrl.websocket("http://192.168.1.40:8000")
        );
        assertEquals("home_assistant", VoiceCatalog.serverMode("original"));
        assertEquals("marin", VoiceCatalog.serverVoice("original"));
        assertEquals("cedar", VoiceCatalog.serverVoice("cedar"));

        WakePhrasePolicy.Decision exact = WakePhrasePolicy.evaluate("Jarvis", "jarvis");
        assertTrue(exact.triggered);
        assertEquals("", exact.command);

        WakePhrasePolicy.Decision command = WakePhrasePolicy.evaluate(
            "Hey Jarvis, where is Amber?",
            "jarvis"
        );
        assertTrue(command.triggered);
        assertEquals("where is amber", command.command);

        WakePhrasePolicy.Decision ignored = WakePhrasePolicy.evaluate(
            "Amber is at home",
            "jarvis"
        );
        assertTrue(!ignored.triggered);

        System.out.println("{'standalone_java_tests': 11, 'status': 'ok'}");
    }

    private static void assertTrue(boolean value) {
        if (!value) throw new AssertionError("Expected true");
    }

    private static void assertEquals(Object expected, Object actual) {
        if (expected == null ? actual != null : !expected.equals(actual)) {
            throw new AssertionError("Expected " + expected + " but got " + actual);
        }
    }
}
