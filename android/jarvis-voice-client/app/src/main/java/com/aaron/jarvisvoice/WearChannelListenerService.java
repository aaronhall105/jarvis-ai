package com.aaron.jarvisvoice;

import android.content.Intent;
import com.google.android.gms.wearable.ChannelClient;
import com.google.android.gms.wearable.WearableListenerService;

/** Lets Play services wake the phone hub before handing over a watch voice channel. */
public final class WearChannelListenerService extends WearableListenerService {
    @Override public void onChannelOpened(ChannelClient.Channel channel) {
        try { startForegroundService(new Intent(this, VoiceService.class).setAction(VoiceService.ACTION_PREPARE_WATCH)); }
        catch (Exception ignored) { }
        WearVoiceBridge.acceptFromSystem(channel);
    }
}
