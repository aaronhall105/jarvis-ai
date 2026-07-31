package com.aaron.jarvisvoice;

import android.graphics.Color;
import android.graphics.Typeface;
import android.text.Spannable;
import android.text.SpannableStringBuilder;
import android.text.style.BackgroundColorSpan;
import android.text.style.RelativeSizeSpan;
import android.text.style.StyleSpan;
import android.text.style.TypefaceSpan;
import android.text.util.Linkify;

import java.util.ArrayList;
import java.util.List;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

public final class ChatTextFormatter {
    private static final Pattern BOLD =
        Pattern.compile("\\*\\*(.+?)\\*\\*");
    private static final Pattern INLINE_CODE =
        Pattern.compile("`([^`\\n]+)`");

    private ChatTextFormatter() {}

    public static CharSequence format(String raw) {
        String value = raw == null ? "" : raw;
        String[] lines = value.split("\\n", -1);
        SpannableStringBuilder output =
            new SpannableStringBuilder();
        boolean codeBlock = false;

        for (int index = 0; index < lines.length; index++) {
            String line = lines[index];
            String trimmed = line.trim();

            if (trimmed.startsWith("```")) {
                codeBlock = !codeBlock;
                continue;
            }

            int start = output.length();
            if (codeBlock) {
                output.append(line);
                output.setSpan(
                    new TypefaceSpan("monospace"),
                    start,
                    output.length(),
                    Spannable.SPAN_EXCLUSIVE_EXCLUSIVE
                );
                output.setSpan(
                    new BackgroundColorSpan(
                        Color.rgb(245, 245, 245)
                    ),
                    start,
                    output.length(),
                    Spannable.SPAN_EXCLUSIVE_EXCLUSIVE
                );
            } else {
                int heading = headingLevel(line);
                String visible = stripHeading(line, heading);
                if (visible.startsWith("- ")) {
                    visible = "• " + visible.substring(2);
                } else if (visible.startsWith("* ")) {
                    visible = "• " + visible.substring(2);
                }
                output.append(visible);
                if (heading > 0 && output.length() > start) {
                    output.setSpan(
                        new StyleSpan(Typeface.BOLD),
                        start,
                        output.length(),
                        Spannable.SPAN_EXCLUSIVE_EXCLUSIVE
                    );
                    output.setSpan(
                        new RelativeSizeSpan(
                            heading == 1 ? 1.25f : 1.12f
                        ),
                        start,
                        output.length(),
                        Spannable.SPAN_EXCLUSIVE_EXCLUSIVE
                    );
                }
                applyInline(output, start, output.length());
            }

            if (index < lines.length - 1) {
                output.append('\n');
            }
        }

        Linkify.addLinks(output, Linkify.WEB_URLS);
        return output;
    }

    private static int headingLevel(String value) {
        String line = value == null ? "" : value;
        if (line.startsWith("### ")) return 3;
        if (line.startsWith("## ")) return 2;
        if (line.startsWith("# ")) return 1;
        return 0;
    }

    private static String stripHeading(
        String value,
        int level
    ) {
        if (level <= 0) return value;
        return value.substring(level + 1);
    }

    private static void applyInline(
        SpannableStringBuilder output,
        int start,
        int end
    ) {
        applyPattern(output, start, end, BOLD, false);
        applyPattern(
            output,
            start,
            output.length(),
            INLINE_CODE,
            true
        );
    }

    private static void applyPattern(
        SpannableStringBuilder output,
        int start,
        int end,
        Pattern pattern,
        boolean code
    ) {
        if (end <= start) return;
        Matcher matcher = pattern.matcher(
            output.subSequence(start, end)
        );
        List<int[]> matches = new ArrayList<>();
        while (matcher.find()) {
            matches.add(new int[] {
                start + matcher.start(),
                start + matcher.end()
            });
        }

        for (int index = matches.size() - 1; index >= 0; index--) {
            int[] range = matches.get(index);
            int open = code ? 1 : 2;
            int close = code ? 1 : 2;
            output.delete(range[1] - close, range[1]);
            output.delete(range[0], range[0] + open);
            int contentEnd = range[1] - open - close;
            if (contentEnd <= range[0]) continue;
            if (code) {
                output.setSpan(
                    new TypefaceSpan("monospace"),
                    range[0],
                    contentEnd,
                    Spannable.SPAN_EXCLUSIVE_EXCLUSIVE
                );
                output.setSpan(
                    new BackgroundColorSpan(
                        Color.rgb(245, 245, 245)
                    ),
                    range[0],
                    contentEnd,
                    Spannable.SPAN_EXCLUSIVE_EXCLUSIVE
                );
            } else {
                output.setSpan(
                    new StyleSpan(Typeface.BOLD),
                    range[0],
                    contentEnd,
                    Spannable.SPAN_EXCLUSIVE_EXCLUSIVE
                );
            }
        }
    }
}
