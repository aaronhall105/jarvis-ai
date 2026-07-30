package com.aaron.jarvisvoice;

import java.util.ArrayList;
import java.util.List;
import java.util.Locale;

/**
 * Rejects transcripts produced by Jarvis hearing his own loudspeaker output.
 *
 * The policy is active only while assistant audio is playing or during a
 * short tail after playback. Outside that window, normal user speech is not
 * rejected as echo.
 */
public final class PlaybackEchoPolicy {
    private PlaybackEchoPolicy() {}

    public static boolean isLikelyEcho(
        String candidate,
        String assistantText,
        boolean playbackActiveOrRecent
    ) {
        if (!playbackActiveOrRecent) return false;

        String heard = normalise(candidate);
        String spoken = normalise(assistantText);

        if (heard.isEmpty() || spoken.isEmpty()) return false;
        if (heard.equals(spoken)) return true;

        List<String> heardWords = words(heard);
        List<String> spokenWords = words(spoken);

        if (heardWords.isEmpty() || spokenWords.isEmpty()) {
            return false;
        }

        if (
            spoken.startsWith(heard + " ")
                || spoken.endsWith(" " + heard)
                || spoken.contains(" " + heard + " ")
                || heard.startsWith(spoken + " ")
        ) {
            return true;
        }

        if (heardWords.size() == 1) {
            String word = heardWords.get(0);
            return word.length() >= 4 && spokenWords.contains(word);
        }

        int contiguous = longestContiguousMatch(
            heardWords,
            spokenWords
        );
        int ordered = longestOrderedMatch(
            heardWords,
            spokenWords
        );
        int tokenOverlap = tokenOverlap(
            heardWords,
            spokenWords
        );

        double orderedCoverage =
            ordered / (double) heardWords.size();
        double overlapCoverage =
            tokenOverlap / (double) heardWords.size();

        if (
            heardWords.size() <= 5
                && ordered == heardWords.size()
        ) {
            return true;
        }

        return (
            contiguous >= 3
                && orderedCoverage >= 0.60
        ) || (
            contiguous >= 2
                && orderedCoverage >= 0.75
        ) || overlapCoverage >= 0.82;
    }

    public static String normalise(String raw) {
        return raw == null
            ? ""
            : raw.toLowerCase(Locale.ROOT)
                .replaceAll("[^a-z0-9' ]+", " ")
                .replaceAll("\\s+", " ")
                .trim();
    }

    private static List<String> words(String value) {
        List<String> result = new ArrayList<>();
        if (value == null || value.isBlank()) return result;

        for (String word : value.split(" ")) {
            if (!word.isBlank()) result.add(word);
        }

        return result;
    }

    private static int longestContiguousMatch(
        List<String> first,
        List<String> second
    ) {
        int[][] lengths =
            new int[first.size() + 1][second.size() + 1];
        int best = 0;

        for (int i = 1; i <= first.size(); i++) {
            for (int j = 1; j <= second.size(); j++) {
                if (first.get(i - 1).equals(second.get(j - 1))) {
                    lengths[i][j] =
                        lengths[i - 1][j - 1] + 1;
                    best = Math.max(best, lengths[i][j]);
                }
            }
        }

        return best;
    }

    private static int longestOrderedMatch(
        List<String> first,
        List<String> second
    ) {
        int[][] lengths =
            new int[first.size() + 1][second.size() + 1];

        for (int i = 1; i <= first.size(); i++) {
            for (int j = 1; j <= second.size(); j++) {
                if (first.get(i - 1).equals(second.get(j - 1))) {
                    lengths[i][j] =
                        lengths[i - 1][j - 1] + 1;
                } else {
                    lengths[i][j] = Math.max(
                        lengths[i - 1][j],
                        lengths[i][j - 1]
                    );
                }
            }
        }

        return lengths[first.size()][second.size()];
    }

    private static int tokenOverlap(
        List<String> first,
        List<String> second
    ) {
        List<String> remaining =
            new ArrayList<>(second);
        int matches = 0;

        for (String word : first) {
            int index = remaining.indexOf(word);
            if (index < 0) continue;
            matches++;
            remaining.remove(index);
        }

        return matches;
    }
}
