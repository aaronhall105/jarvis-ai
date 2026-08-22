package com.aaron.jarvisvoice;

import java.util.ArrayList;
import java.util.List;

final class DeveloperEndpointPolicy {
    private DeveloperEndpointPolicy() {}
    static List<String> order(boolean localNetwork, String local, String remote) {
        ArrayList<String> values = new ArrayList<>();
        String first = localNetwork ? local : remote;
        String second = localNetwork ? remote : local;
        if (first != null && !first.isBlank()) values.add(first.trim());
        if (second != null && !second.isBlank() && !values.contains(second.trim())) values.add(second.trim());
        return values;
    }
}
