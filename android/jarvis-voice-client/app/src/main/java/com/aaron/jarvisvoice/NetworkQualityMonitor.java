package com.aaron.jarvisvoice;

import android.content.Context;
import android.net.ConnectivityManager;
import android.net.Network;
import android.net.NetworkCapabilities;

final class NetworkQualityMonitor implements AutoCloseable {
    interface Listener {
        void onNetworkAvailable();
        void onNetworkLost();
    }

    private final ConnectivityManager manager;
    private final ConnectivityManager.NetworkCallback callback;
    private final Listener listener;
    private volatile boolean available;
    private boolean registered;

    NetworkQualityMonitor(Context context, Listener listener) {
        this.listener = listener;
        manager = context.getSystemService(ConnectivityManager.class);
        available = calculateAvailable();
        callback = new ConnectivityManager.NetworkCallback() {
            @Override public void onAvailable(Network network) {
                boolean changed = !available;
                available = true;
                if (changed) listener.onNetworkAvailable();
            }

            @Override public void onCapabilitiesChanged(
                Network network,
                NetworkCapabilities capabilities
            ) {
                boolean next = hasInternet(capabilities);
                boolean changed = next != available;
                available = next;
                if (!changed) return;
                if (next) listener.onNetworkAvailable();
                else listener.onNetworkLost();
            }

            @Override public void onLost(Network network) {
                boolean next = calculateAvailable();
                boolean changed = next != available;
                available = next;
                if (!changed) return;
                if (next) listener.onNetworkAvailable();
                else listener.onNetworkLost();
            }
        };

        if (manager != null) {
            try {
                manager.registerDefaultNetworkCallback(callback);
                registered = true;
            } catch (Exception ignored) {
                registered = false;
            }
        }
    }

    boolean isAvailable() {
        return available;
    }

    private boolean calculateAvailable() {
        if (manager == null) return true;
        try {
            Network active = manager.getActiveNetwork();
            return active != null
                && hasInternet(manager.getNetworkCapabilities(active));
        } catch (Exception ignored) {
            return true;
        }
    }

    private static boolean hasInternet(NetworkCapabilities capabilities) {
        return capabilities != null
            && capabilities.hasCapability(
                NetworkCapabilities.NET_CAPABILITY_INTERNET
            );
    }

    @Override public void close() {
        if (!registered || manager == null) return;
        registered = false;
        try {
            manager.unregisterNetworkCallback(callback);
        } catch (Exception ignored) {}
    }
}
