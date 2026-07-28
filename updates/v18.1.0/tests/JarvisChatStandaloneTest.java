import com.aaron.jarvisvoice.AudioFrameSizer;
import com.aaron.jarvisvoice.ConversationMode;
import com.aaron.jarvisvoice.CoreUrl;
import com.aaron.jarvisvoice.VoiceCatalog;
import com.aaron.jarvisvoice.WakePhrasePolicy;

public final class JarvisChatStandaloneTest {
    private static int checks = 0;

    private static void check(boolean condition, String message) {
        checks++;
        if (!condition) throw new AssertionError(message);
    }

    public static void main(String[] args) throws Exception {
        check("live".equals(ConversationMode.normalise("LIVE")), "Live mode normalisation failed");
        check("standard".equals(ConversationMode.normalise("standard")), "Standard mode normalisation failed");
        check("standard".equals(ConversationMode.toggle("live")), "Mode toggle failed");
        check(AudioFrameSizer.bytesFor(24_000, 20) == 960, "20 ms PCM frame size failed");
        check(
            "ws://192.168.1.40:8000/api/realtime/voice".equals(CoreUrl.websocket("http://192.168.1.40:8000")),
            "Core WebSocket URL failed"
        );
        check("home_assistant".equals(VoiceCatalog.serverMode("original")), "Original voice mode failed");
        check("cedar".equals(VoiceCatalog.serverVoice("cedar")), "Realtime voice selection failed");
        WakePhrasePolicy.Decision wake = WakePhrasePolicy.evaluate("Hey Jarvis, where is Amber?", "jarvis");
        check(wake.triggered, "Wake phrase did not trigger");
        check("where is amber".equals(wake.command), "Wake command extraction failed: " + wake.command);
        System.out.println("{\"java_contract_checks\":" + checks + ",\"status\":\"ok\"}");
    }
}
