package com.aaron.jarvisvoice;

import android.content.Context;
import android.media.AudioAttributes;
import android.media.MediaPlayer;
import android.net.Uri;
import android.util.Log;

import java.net.URI;
import java.util.HashMap;
import java.util.Map;

public final class PlaybackController {
    private static final String TAG = "JarvisVoiceOutput";
    public interface Listener {
        void onPlaybackStarted();
        void onPlaybackCompleted();
        void onPlaybackError(String error);
    }

    private final Context context;
    private final Listener listener;
    private MediaPlayer player;

    public PlaybackController(Context context, Listener listener) {
        this.context = context.getApplicationContext();
        this.listener = listener;
    }

    public boolean isPlaying() {
        try { return player != null && player.isPlaying(); } catch (Exception ignored) { return false; }
    }

    public void play(String baseUrl, String path, String token) {
        stop();
        try {
            String resolved = resolve(baseUrl, path);
            MediaPlayer mediaPlayer = new MediaPlayer();
            mediaPlayer.setAudioAttributes(new AudioAttributes.Builder()
                .setUsage(AudioAttributes.USAGE_ASSISTANT)
                .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH)
                .build());
            Map<String, String> headers = new HashMap<>();
            if (token != null && !token.isBlank()) headers.put("Authorization", "Bearer " + token);
            mediaPlayer.setDataSource(context, Uri.parse(resolved), headers);
            mediaPlayer.setOnPreparedListener(value -> {
                player = value;
                value.start();
                Log.i(TAG, "VOICE_AUDIO_PLAYER_STARTED mechanism=MediaPlayer");
                listener.onPlaybackStarted();
            });
            mediaPlayer.setOnCompletionListener(value -> {
                release(value);
                if (player == value) player = null;
                listener.onPlaybackCompleted();
            });
            mediaPlayer.setOnErrorListener((value, what, extra) -> {
                Log.w(TAG, "VOICE_AUDIO_PLAYER_ERROR what=" + what + " extra=" + extra);
                release(value);
                if (player == value) player = null;
                listener.onPlaybackError("MediaPlayer error " + what + "/" + extra);
                return true;
            });
            player = mediaPlayer;
            mediaPlayer.prepareAsync();
        } catch (Exception exception) {
            stop();
            listener.onPlaybackError(exception.getMessage());
        }
    }

    public void stop() {
        MediaPlayer existing = player;
        player = null;
        if (existing != null) {
            try { existing.stop(); } catch (Exception ignored) {}
            release(existing);
        }
    }

    private static void release(MediaPlayer value) {
        try { value.reset(); } catch (Exception ignored) {}
        try { value.release(); } catch (Exception ignored) {}
    }

    static String resolve(String baseUrl, String path) throws Exception {
        if (path == null || path.isBlank()) return "";
        URI candidate = URI.create(path);
        if (candidate.isAbsolute()) return candidate.toString();
        return URI.create(baseUrl.endsWith("/") ? baseUrl : baseUrl + "/").resolve(path.startsWith("/") ? path.substring(1) : path).toString();
    }
}
