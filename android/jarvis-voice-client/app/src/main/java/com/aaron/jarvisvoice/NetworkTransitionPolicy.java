package com.aaron.jarvisvoice;

/** Keeps transport changes distinct from simple internet-capability availability. */
final class NetworkTransitionPolicy {
    static final int NONE = 0, WIFI = 1, CELLULAR = 2, ETHERNET = 3, OTHER = 4;
    private NetworkTransitionPolicy() {}
    static boolean shouldReevaluate(boolean wasAvailable, boolean available, int previous, int current) {
        return wasAvailable != available || (available && previous != current);
    }
}
