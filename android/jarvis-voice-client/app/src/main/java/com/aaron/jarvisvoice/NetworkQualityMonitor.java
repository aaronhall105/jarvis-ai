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
    private volatile int transport;
    private boolean registered;

    NetworkQualityMonitor(Context context, Listener listener) {
        this.listener = listener;
        manager = context.getSystemService(ConnectivityManager.class);
        available = calculateAvailable();
        transport = calculateTransport();
        callback = new ConnectivityManager.NetworkCallback() {
            @Override public void onAvailable(Network network) {
                int nextTransport = transport(manager == null ? null : manager.getNetworkCapabilities(network));
                boolean changed = NetworkTransitionPolicy.shouldReevaluate(
                    available, true, transport, nextTransport
                );
                available = true;
                transport = nextTransport;
                if (changed) listener.onNetworkAvailable();
            }

            @Override public void onCapabilitiesChanged(
                Network network,
                NetworkCapabilities capabilities
            ) {
                boolean next = hasInternet(capabilities);
                int nextTransport = transport(capabilities);
                boolean changed = NetworkTransitionPolicy.shouldReevaluate(
                    available, next, transport, nextTransport
                );
                available = next;
                transport = nextTransport;
                if (!changed) return;
                if (next) listener.onNetworkAvailable();
                else listener.onNetworkLost();
            }

            @Override public void onLost(Network network) {
                boolean next = calculateAvailable();
                int nextTransport = calculateTransport();
                boolean changed = NetworkTransitionPolicy.shouldReevaluate(
                    available, next, transport, nextTransport
                );
                available = next;
                transport = nextTransport;
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

    private int calculateTransport() {
        if (manager == null) return NetworkTransitionPolicy.OTHER;
        try { return transport(manager.getNetworkCapabilities(manager.getActiveNetwork())); }
        catch (Exception ignored) { return NetworkTransitionPolicy.OTHER; }
    }

    private static int transport(NetworkCapabilities capabilities) {
        if (capabilities == null) return NetworkTransitionPolicy.NONE;
        if (capabilities.hasTransport(NetworkCapabilities.TRANSPORT_WIFI)) return NetworkTransitionPolicy.WIFI;
        if (capabilities.hasTransport(NetworkCapabilities.TRANSPORT_CELLULAR)) return NetworkTransitionPolicy.CELLULAR;
        if (capabilities.hasTransport(NetworkCapabilities.TRANSPORT_ETHERNET)) return NetworkTransitionPolicy.ETHERNET;
        return NetworkTransitionPolicy.OTHER;
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
