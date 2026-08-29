package com.aaron.jarvisvoice;

final class DeveloperThreadPolicy {
    private DeveloperThreadPolicy() { }

    static String displayTitle(String name, String preview) {
        String cleanName = clean(name);
        if (!cleanName.isBlank()) return cleanName;
        String cleanPreview = clean(preview);
        return cleanPreview.isBlank() ? "Development chat" : cleanPreview;
    }

    private static String clean(String value) {
        if (value == null) return "";
        String clean = value.trim();
        return "null".equalsIgnoreCase(clean) ? "" : clean;
    }
}
