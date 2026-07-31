package com.aaron.jarvisvoice;

import android.content.Context;
import android.media.AudioDeviceCallback;
import android.media.AudioDeviceInfo;
import android.media.AudioManager;
import android.os.Handler;
import android.os.Looper;

import java.util.LinkedHashSet;
import java.util.Set;

public final class AudioRouteMonitor implements AutoCloseable {
    private final AudioManager audio;
    private final VoiceDiagnosticsStore diagnostics;
    private final Handler main = new Handler(Looper.getMainLooper());
    private boolean started;

    private final AudioDeviceCallback callback =
        new AudioDeviceCallback() {
            @Override public void onAudioDevicesAdded(
                AudioDeviceInfo[] addedDevices
            ) {
                record("Audio route added");
            }

            @Override public void onAudioDevicesRemoved(
                AudioDeviceInfo[] removedDevices
            ) {
                record("Audio route removed");
            }
        };

    public AudioRouteMonitor(Context context) {
        Context app = context.getApplicationContext();
        audio = (AudioManager) app.getSystemService(
            Context.AUDIO_SERVICE
        );
        diagnostics = new VoiceDiagnosticsStore(app);
    }

    public void start() {
        if (started || audio == null) return;
        started = true;
        audio.registerAudioDeviceCallback(callback, main);
        record("Audio route active");
    }

    public boolean usesPrivateListeningRoute() {
        if (audio == null) return false;
        AudioDeviceInfo selected = audio.getCommunicationDevice();
        if (selected == null) return false;
        return switch (selected.getType()) {
            case AudioDeviceInfo.TYPE_BLUETOOTH_SCO,
                 AudioDeviceInfo.TYPE_BLUETOOTH_A2DP,
                 AudioDeviceInfo.TYPE_WIRED_HEADSET,
                 AudioDeviceInfo.TYPE_WIRED_HEADPHONES,
                 AudioDeviceInfo.TYPE_USB_DEVICE,
                 AudioDeviceInfo.TYPE_USB_HEADSET,
                 AudioDeviceInfo.TYPE_BLE_HEADSET -> true;
            default -> false;
        };
    }

    @Override public void close() {
        if (!started || audio == null) return;
        started = false;
        audio.unregisterAudioDeviceCallback(callback);
        record("Audio route monitor stopped");
    }

    private void record(String event) {
        if (audio == null) {
            diagnostics.recordAudioRoute(event + " · unavailable");
            return;
        }

        Set<String> inputs = names(
            audio.getDevices(AudioManager.GET_DEVICES_INPUTS)
        );
        Set<String> outputs = names(
            audio.getDevices(AudioManager.GET_DEVICES_OUTPUTS)
        );
        diagnostics.recordAudioRoute(
            event + " · input " + join(inputs)
                + " · output " + join(outputs)
        );
    }

    private static Set<String> names(AudioDeviceInfo[] devices) {
        Set<String> values = new LinkedHashSet<>();
        if (devices == null) return values;
        for (AudioDeviceInfo device : devices) {
            values.add(typeName(device.getType()));
        }
        return values;
    }

    private static String join(Set<String> values) {
        return values.isEmpty()
            ? "none"
            : String.join(", ", values);
    }

    private static String typeName(int type) {
        return switch (type) {
            case AudioDeviceInfo.TYPE_BUILTIN_MIC -> "built-in mic";
            case AudioDeviceInfo.TYPE_BUILTIN_SPEAKER ->
                "phone speaker";
            case AudioDeviceInfo.TYPE_BLUETOOTH_SCO ->
                "Bluetooth headset";
            case AudioDeviceInfo.TYPE_BLUETOOTH_A2DP ->
                "Bluetooth audio";
            case AudioDeviceInfo.TYPE_WIRED_HEADSET ->
                "wired headset";
            case AudioDeviceInfo.TYPE_WIRED_HEADPHONES ->
                "wired headphones";
            case AudioDeviceInfo.TYPE_USB_DEVICE,
                 AudioDeviceInfo.TYPE_USB_HEADSET ->
                "USB audio";
            case AudioDeviceInfo.TYPE_BLE_HEADSET ->
                "BLE headset";
            case AudioDeviceInfo.TYPE_BLE_SPEAKER ->
                "BLE speaker";
            default -> "route-" + type;
        };
    }
}
