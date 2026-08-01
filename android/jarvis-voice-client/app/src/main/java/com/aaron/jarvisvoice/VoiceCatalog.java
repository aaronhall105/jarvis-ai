package com.aaron.jarvisvoice;

import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import java.util.Locale;

public final class VoiceCatalog {
    public static final String ORIGINAL_ID = "original";
    public static final String HOME_ASSISTANT_ID =
        "home_assistant_original";
    public static final String MODE_HOME_ASSISTANT =
        "home_assistant";
    public static final String MODE_REALTIME = "realtime";

    public static final class Entry {
        public final String id;
        public final String label;
        public final String mode;

        private Entry(
            String id,
            String label,
            String mode
        ) {
            this.id = id;
            this.label = label;
            this.mode = mode;
        }
    }

    private static final List<Entry> ENTRIES;

    static {
        List<Entry> values = new ArrayList<>();

        values.add(new Entry(
            ORIGINAL_ID,
            "Jarvis — original Home Assistant voice",
            MODE_HOME_ASSISTANT
        ));
        values.add(new Entry(
            "cedar",
            "Cedar — warm and confident",
            MODE_REALTIME
        ));
        values.add(new Entry(
            "echo",
            "Echo — clear and direct",
            MODE_REALTIME
        ));
        values.add(new Entry(
            "marin",
            "Marin — natural",
            MODE_REALTIME
        ));
        values.add(new Entry(
            "alloy",
            "Alloy",
            MODE_REALTIME
        ));
        values.add(new Entry(
            "ash",
            "Ash",
            MODE_REALTIME
        ));
        values.add(new Entry(
            "ballad",
            "Ballad",
            MODE_REALTIME
        ));
        values.add(new Entry(
            "coral",
            "Coral",
            MODE_REALTIME
        ));
        values.add(new Entry(
            "sage",
            "Sage",
            MODE_REALTIME
        ));
        values.add(new Entry(
            "shimmer",
            "Shimmer",
            MODE_REALTIME
        ));
        values.add(new Entry(
            "verse",
            "Verse",
            MODE_REALTIME
        ));

        ENTRIES = Collections.unmodifiableList(values);
    }

    private VoiceCatalog() {}

    public static List<Entry> entries() {
        return ENTRIES;
    }

    public static List<String> labels() {
        List<String> values = new ArrayList<>();
        for (Entry entry : ENTRIES) {
            values.add(entry.label);
        }
        return values;
    }

    public static Entry fromId(String value) {
        String id = value == null
            ? ""
            : value.trim().toLowerCase(Locale.ROOT);

        if (HOME_ASSISTANT_ID.equals(id)) {
            return ENTRIES.get(0);
        }

        for (Entry entry : ENTRIES) {
            if (entry.id.equals(id)) {
                return entry;
            }
        }

        return ENTRIES.get(0);
    }

    public static Entry at(int index) {
        if (index < 0 || index >= ENTRIES.size()) {
            return ENTRIES.get(0);
        }
        return ENTRIES.get(index);
    }

    public static int indexOf(String id) {
        Entry selected = fromId(id);
        return ENTRIES.indexOf(selected);
    }

    public static boolean isOriginal(String id) {
        return MODE_HOME_ASSISTANT.equals(
            fromId(id).mode
        );
    }

    public static String serverVoice(String id) {
        Entry entry = fromId(id);
        return MODE_REALTIME.equals(entry.mode)
            ? entry.id
            : "marin";
    }

    public static String serverMode(String id) {
        return fromId(id).mode;
    }
}
