import com.aaron.jarvisvoice.AudioFrameSizer;
import com.aaron.jarvisvoice.CoreUrl;

public final class RealtimeStandaloneTest {
    private static int tests = 0;

    private static void equal(Object expected, Object actual) {
        tests++;
        if (!expected.equals(actual)) {
            throw new AssertionError("Expected " + expected + " but got " + actual);
        }
    }

    private static void truth(boolean value) {
        tests++;
        if (!value) throw new AssertionError("Expected true");
    }

    public static void main(String[] args) throws Exception {
        equal(960, AudioFrameSizer.bytesFor(24_000, 20));
        equal("ws://192.168.1.40:8000/api/realtime/voice", CoreUrl.websocket("http://192.168.1.40:8000"));
        equal("wss://jarvis.example.ts.net/api/realtime/voice", CoreUrl.websocket("https://jarvis.example.ts.net/"));
        equal("ws://host:8000/api/realtime/voice", CoreUrl.websocket("ws://host:8000/api/realtime/voice"));
        boolean failed = false;
        try { CoreUrl.websocket("ftp://host"); } catch (IllegalArgumentException expected) { failed = true; }
        truth(failed);
        System.out.println("{\"standalone_java_tests\":" + tests + ",\"status\":\"ok\"}");
    }
}
