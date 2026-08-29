package com.aaron.jarvisvoice.wear;

import android.os.Bundle;
import android.service.voice.VoiceInteractionService;

public final class WearAssistantService extends VoiceInteractionService {
    @Override public void onLaunchVoiceAssistFromKeyguard() { showSession(new Bundle(), 0); }
}
