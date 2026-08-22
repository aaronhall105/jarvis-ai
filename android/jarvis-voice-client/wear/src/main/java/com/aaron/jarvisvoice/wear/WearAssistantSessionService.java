package com.aaron.jarvisvoice.wear;

import android.content.Intent;
import android.os.Bundle;
import android.service.voice.VoiceInteractionSession;
import android.service.voice.VoiceInteractionSessionService;

public final class WearAssistantSessionService extends VoiceInteractionSessionService {
    @Override public VoiceInteractionSession onNewSession(Bundle args) {
        return new VoiceInteractionSession(this) {
            @Override public void onShow(Bundle arguments, int flags) {
                super.onShow(arguments, flags);
                startAssistantActivity(
                    new Intent(
                        WearAssistantSessionService.this,
                        JarvisWearActivity.class
                    ).putExtra(JarvisWearActivity.EXTRA_AUTO_START, true)
                );
                hide();
            }
        };
    }
}
