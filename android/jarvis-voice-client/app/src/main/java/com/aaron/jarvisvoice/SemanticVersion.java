package com.aaron.jarvisvoice;

import java.util.Objects;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

public final class SemanticVersion implements Comparable<SemanticVersion> {
    private static final Pattern PATTERN = Pattern.compile(
        "^(0|[1-9]\\d*)\\.(0|[1-9]\\d*)\\.(0|[1-9]\\d*)(?:-(alpha|beta)([1-9]\\d*))?$"
    );
    public final int major, minor, patch, prereleaseNumber;
    public final UpdateChannel channel;

    private SemanticVersion(int major, int minor, int patch, UpdateChannel channel, int number) {
        this.major = major; this.minor = minor; this.patch = patch;
        this.channel = channel; this.prereleaseNumber = number;
    }

    public static SemanticVersion parse(String value) {
        Matcher matcher = PATTERN.matcher(Objects.requireNonNull(value, "version"));
        if (!matcher.matches()) throw new IllegalArgumentException("Malformed release version");
        String pre = matcher.group(4);
        return new SemanticVersion(
            Integer.parseInt(matcher.group(1)), Integer.parseInt(matcher.group(2)),
            Integer.parseInt(matcher.group(3)),
            pre == null ? UpdateChannel.STABLE : UpdateChannel.parse(pre),
            pre == null ? 0 : Integer.parseInt(matcher.group(5))
        );
    }

    @Override public int compareTo(SemanticVersion other) {
        int result = Integer.compare(major, other.major);
        if (result == 0) result = Integer.compare(minor, other.minor);
        if (result == 0) result = Integer.compare(patch, other.patch);
        if (result != 0) return result;
        result = Integer.compare(rank(channel), rank(other.channel));
        return result != 0 ? result : Integer.compare(prereleaseNumber, other.prereleaseNumber);
    }

    private static int rank(UpdateChannel channel) {
        return switch (channel) { case ALPHA -> 0; case BETA -> 1; case STABLE -> 2; };
    }
}
