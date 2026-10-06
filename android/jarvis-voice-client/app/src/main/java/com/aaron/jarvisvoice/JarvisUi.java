package com.aaron.jarvisvoice;

import android.content.Context;
import android.graphics.Color;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.graphics.drawable.InsetDrawable;
import android.view.Gravity;
import android.widget.ImageButton;
import android.widget.TextView;

/** Shared visual tokens and primitives for the phone Jarvis interface. */
final class JarvisUi {
    static final int BLACK = Color.rgb(20, 20, 20);
    static final int MID = Color.rgb(103, 103, 103);
    static final int LINE = Color.rgb(226, 226, 226);
    static final int SOFT = Color.rgb(246, 246, 246);
    static final int WHITE = Color.WHITE;
    static final int DANGER = Color.rgb(190, 36, 46);

    static final int SPACE_4 = 4;
    static final int SPACE_8 = 8;
    static final int SPACE_12 = 12;
    static final int SPACE_16 = 16;
    static final int SPACE_20 = 20;
    static final int SPACE_24 = 24;
    static final int SPACE_32 = 32;

    static final int RADIUS_SMALL = 12;
    static final int RADIUS_MEDIUM = 18;
    static final int RADIUS_LARGE = 24;
    static final int RADIUS_PILL = 999;

    static final int PAGE_MARGIN = 16;
    static final int HEADER_TOP = 9;
    static final int HEADER_BOTTOM = 9;
    static final int TOUCH_TARGET = 48;
    static final int ACTION_VISUAL = 36;
    static final int PRIMARY_NAV_HEIGHT = 48;
    static final int FILTER_HEIGHT = 44;

    private JarvisUi() {}

    static int dp(Context context, int value) {
        return Math.round(value * context.getResources().getDisplayMetrics().density);
    }

    static GradientDrawable rounded(
        Context context,
        int fill,
        int radiusDp,
        int strokeDp,
        int strokeColour
    ) {
        GradientDrawable background = new GradientDrawable();
        background.setColor(fill);
        background.setCornerRadius(dp(context, radiusDp));
        if (strokeDp > 0) background.setStroke(dp(context, strokeDp), strokeColour);
        return background;
    }

    static TextView text(Context context, String value, float size, int colour) {
        TextView view = new TextView(context);
        view.setText(value);
        view.setTextSize(size);
        view.setTextColor(colour);
        view.setTypeface(Typeface.create("sans-serif", Typeface.NORMAL));
        view.setLineSpacing(0, 1.08f);
        return view;
    }

    static ImageButton actionButton(
        Context context,
        int icon,
        String description,
        int foreground
    ) {
        ImageButton button = new ImageButton(context);
        button.setImageResource(icon);
        button.setContentDescription(description);
        button.setColorFilter(foreground);
        button.setScaleType(ImageButton.ScaleType.CENTER);
        button.setPadding(
            dp(context, 13),
            dp(context, 13),
            dp(context, 13),
            dp(context, 13)
        );
        button.setMinimumWidth(0);
        button.setMinimumHeight(0);
        button.setElevation(0f);
        button.setStateListAnimator(null);
        int inset = (TOUCH_TARGET - ACTION_VISUAL) / 2;
        button.setBackground(new InsetDrawable(
            rounded(context, SOFT, RADIUS_PILL, 1, LINE),
            dp(context, inset)
        ));
        return button;
    }

    static TextView segmentedItem(Context context, String label, boolean selected) {
        TextView tab = text(context, label, 14, selected ? WHITE : BLACK);
        tab.setGravity(Gravity.CENTER);
        tab.setTypeface(Typeface.create("sans-serif-medium", Typeface.NORMAL));
        tab.setBackground(rounded(
            context,
            selected ? BLACK : Color.TRANSPARENT,
            RADIUS_PILL,
            0,
            Color.TRANSPARENT
        ));
        tab.setContentDescription(label + (selected ? ", selected" : ""));
        tab.setElevation(0f);
        tab.setStateListAnimator(null);
        return tab;
    }
}
