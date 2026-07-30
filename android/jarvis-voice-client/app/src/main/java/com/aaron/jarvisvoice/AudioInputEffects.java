package com.aaron.jarvisvoice;

import android.media.AudioRecord;
import android.media.audiofx.AcousticEchoCanceler;
import android.media.audiofx.AutomaticGainControl;
import android.media.audiofx.NoiseSuppressor;

final class AudioInputEffects {
    private AcousticEchoCanceler echoCanceler;
    private NoiseSuppressor noiseSuppressor;
    private AutomaticGainControl gainControl;

    private boolean aecEnabled;
    private boolean nsEnabled;
    private boolean agcEnabled;

    static AudioInputEffects attach(AudioRecord recorder) {
        AudioInputEffects effects = new AudioInputEffects();
        effects.attachInternal(recorder);
        return effects;
    }

    private void attachInternal(AudioRecord recorder) {
        if (recorder == null) return;

        int sessionId = recorder.getAudioSessionId();

        if (AcousticEchoCanceler.isAvailable()) {
            try {
                echoCanceler =
                    AcousticEchoCanceler.create(sessionId);
                if (echoCanceler != null) {
                    echoCanceler.setEnabled(true);
                    aecEnabled = echoCanceler.getEnabled();
                }
            } catch (Throwable ignored) {
                releaseEcho();
            }
        }

        if (NoiseSuppressor.isAvailable()) {
            try {
                noiseSuppressor =
                    NoiseSuppressor.create(sessionId);
                if (noiseSuppressor != null) {
                    noiseSuppressor.setEnabled(true);
                    nsEnabled = noiseSuppressor.getEnabled();
                }
            } catch (Throwable ignored) {
                releaseNoise();
            }
        }

        if (AutomaticGainControl.isAvailable()) {
            try {
                gainControl =
                    AutomaticGainControl.create(sessionId);
                if (gainControl != null) {
                    gainControl.setEnabled(true);
                    agcEnabled = gainControl.getEnabled();
                }
            } catch (Throwable ignored) {
                releaseGain();
            }
        }
    }

    String summary() {
        return "AEC " + label(aecEnabled)
            + " · NS " + label(nsEnabled)
            + " · AGC " + label(agcEnabled);
    }

    void release() {
        releaseEcho();
        releaseNoise();
        releaseGain();
    }

    private void releaseEcho() {
        if (echoCanceler != null) {
            try {
                echoCanceler.release();
            } catch (Throwable ignored) {}
        }
        echoCanceler = null;
        aecEnabled = false;
    }

    private void releaseNoise() {
        if (noiseSuppressor != null) {
            try {
                noiseSuppressor.release();
            } catch (Throwable ignored) {}
        }
        noiseSuppressor = null;
        nsEnabled = false;
    }

    private void releaseGain() {
        if (gainControl != null) {
            try {
                gainControl.release();
            } catch (Throwable ignored) {}
        }
        gainControl = null;
        agcEnabled = false;
    }

    private static String label(boolean enabled) {
        return enabled ? "on" : "unavailable";
    }
}
