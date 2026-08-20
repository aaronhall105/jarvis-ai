package com.aaron.jarvisvoice;

import java.util.Locale;

public enum UpdateChannel {
    STABLE, BETA, ALPHA;

    public static UpdateChannel parse(String value) {
        try { return valueOf(value.trim().toUpperCase(Locale.ROOT)); }
        catch (Exception exception) { throw new IllegalArgumentException("Unknown update channel"); }
    }

    public boolean accepts(UpdateChannel release) {
        return switch (this) {
            case STABLE -> release == STABLE;
            case BETA -> release != ALPHA;
            case ALPHA -> true;
        };
    }
}
