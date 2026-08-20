package com.aaron.jarvisvoice;

import android.Manifest;
import android.content.Context;
import android.content.pm.PackageManager;
import android.media.AudioManager;
import android.os.Handler;
import android.os.Looper;
import android.os.SystemClock;

import java.time.OffsetDateTime;
import java.time.ZoneId;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;

import okhttp3.OkHttpClient;
import okhttp3.Request;
import okhttp3.Response;

public final class JarvisSystemTest {
    public interface Callback {
        void onComplete(Result result);
    }

    public static final class Result {
        public final boolean passed;
        public final String report;

        Result(boolean passed, String report) {
            this.passed = passed;
            this.report = report;
        }
    }

    private static final String LONDON = "Europe/London";

    private final Context context;
    private final Handler main = new Handler(Looper.getMainLooper());
    private final ExecutorService executor =
        Executors.newSingleThreadExecutor();
    private final OkHttpClient healthClient = new OkHttpClient.Builder()
        .connectTimeout(2500, TimeUnit.MILLISECONDS)
        .readTimeout(2500, TimeUnit.MILLISECONDS)
        .callTimeout(3500, TimeUnit.MILLISECONDS)
        .retryOnConnectionFailure(false)
        .build();

    public JarvisSystemTest(Context context) {
        this.context = context.getApplicationContext();
    }

    public void run(
        String configuredLanUrl,
        String configuredRemoteUrl,
        Callback callback
    ) {
        executor.execute(() -> {
            String lan = CoreEndpointSelector.normaliseBaseUrl(
                configuredLanUrl
            );
            String remote = CoreEndpointSelector.normaliseOptionalBaseUrl(
                configuredRemoteUrl
            );
            Probe lanProbe = probe(lan);
            Probe remoteProbe = remote.isBlank()
                ? new Probe(false, 0L, "Not configured")
                : probe(remote);

            boolean timezonePass;
            String timezoneDetail;
            try {
                OffsetDateTime london = OffsetDateTime.now(
                    ZoneId.of(LONDON)
                );
                timezonePass = true;
                timezoneDetail = london.toString();
            } catch (Exception exception) {
                timezonePass = false;
                timezoneDetail = safeMessage(exception);
            }

            boolean microphonePass =
                context.checkSelfPermission(Manifest.permission.RECORD_AUDIO)
                    == PackageManager.PERMISSION_GRANTED;

            AudioManager audio = (AudioManager) context.getSystemService(
                Context.AUDIO_SERVICE
            );
            int outputs = audio == null
                ? 0
                : audio.getDevices(
                    AudioManager.GET_DEVICES_OUTPUTS
                ).length;
            boolean audioPass = outputs > 0;

            VoiceDiagnosticsStore diagnostics =
                new VoiceDiagnosticsStore(context);
            boolean continuity = diagnostics.hasConversationContext();
            String selected = lanProbe.passed
                ? "LAN"
                : remoteProbe.passed ? "Remote" : "Unavailable";

            boolean passed = (
                (lanProbe.passed || remoteProbe.passed)
                    && timezonePass
                    && microphonePass
                    && audioPass
            );

            String report =
                "Jarvis alpha6 system test\n"
                    + "LAN health: " + line(lanProbe) + "\n"
                    + "Remote health: " + line(remoteProbe) + "\n"
                    + "Selected endpoint: " + selected + "\n"
                    + "Microphone permission: "
                    + passFail(microphonePass) + "\n"
                    + "Audio output: " + passFail(audioPass)
                    + " · " + outputs + " route(s)\n"
                    + "London local time: " + passFail(timezonePass)
                    + " · " + timezoneDetail + "\n"
                    + "Conversation continuity: "
                    + (continuity ? "PASS" : "NOT YET OBSERVED")
                    + " · "
                    + diagnostics.conversationContextSummary() + "\n"
                    + "Current diagnostic endpoint: "
                    + diagnostics.activeEndpointSummary() + "\n"
                    + "Overall: " + (passed ? "PASS" : "ATTENTION");

            diagnostics.recordSystemTest(passed, report);
            Result result = new Result(passed, report);
            main.post(() -> callback.onComplete(result));
            executor.shutdown();
        });
    }

    private Probe probe(String baseUrl) {
        long started = SystemClock.elapsedRealtime();
        try {
            Request request = new Request.Builder()
                .url(CoreEndpointSelector.healthUrl(baseUrl))
                .get()
                .build();
            try (Response response = healthClient.newCall(request).execute()) {
                long latency = Math.max(
                    0L,
                    SystemClock.elapsedRealtime() - started
                );
                return new Probe(
                    response.isSuccessful(),
                    latency,
                    "HTTP " + response.code()
                );
            }
        } catch (Exception exception) {
            return new Probe(
                false,
                Math.max(
                    0L,
                    SystemClock.elapsedRealtime() - started
                ),
                safeMessage(exception)
            );
        }
    }

    private static String line(Probe probe) {
        return passFail(probe.passed)
            + " · " + probe.latencyMs + " ms · " + probe.detail;
    }

    private static String passFail(boolean value) {
        return value ? "PASS" : "FAIL";
    }

    private static String safeMessage(Exception exception) {
        String value = exception.getMessage();
        return value == null || value.isBlank()
            ? exception.getClass().getSimpleName()
            : value;
    }

    private static final class Probe {
        final boolean passed;
        final long latencyMs;
        final String detail;

        Probe(boolean passed, long latencyMs, String detail) {
            this.passed = passed;
            this.latencyMs = latencyMs;
            this.detail = detail;
        }
    }
}
