package com.aaron.jarvisvoice;

import android.app.Activity;
import android.graphics.Insets;
import android.graphics.Typeface;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.widget.ImageButton;
import android.widget.LinearLayout;
import android.widget.TextView;

/** One visual header and primary navigation shell shared by Home, Chat and Tasks. */
final class JarvisAppShell {
    enum Destination { HOME, CHAT, TASKS }

    interface Actions {
        void onMode();
        void onNotifications();
        void onNewChat();
        void onClearChat();
        void onSettings();
        default void onHome() {}
        void onChat();
        void onTasks();
    }

    static final class Header {
        final LinearLayout view;
        final LinearLayout topBar;
        final LinearLayout navigationFrame;
        final TextView mode;
        final TextView context;
        final TextView homeTab;
        final TextView chatTab;
        final TextView tasksTab;
        final ImageButton notifications;
        final ImageButton newChat;
        final ImageButton clearChat;
        final ImageButton settings;

        Header(
            LinearLayout view,
            LinearLayout topBar,
            LinearLayout navigationFrame,
            TextView mode,
            TextView context,
            TextView homeTab,
            TextView chatTab,
            TextView tasksTab,
            ImageButton notifications,
            ImageButton newChat,
            ImageButton clearChat,
            ImageButton settings
        ) {
            this.view = view;
            this.topBar = topBar;
            this.navigationFrame = navigationFrame;
            this.mode = mode;
            this.context = context;
            this.homeTab = homeTab;
            this.chatTab = chatTab;
            this.tasksTab = tasksTab;
            this.notifications = notifications;
            this.newChat = newChat;
            this.clearChat = clearChat;
            this.settings = settings;
        }

        void applySystemInsets(Insets bars) {
            topBar.setPadding(
                JarvisUi.dp(topBar.getContext(), JarvisUi.PAGE_MARGIN) + bars.left,
                JarvisUi.dp(topBar.getContext(), JarvisUi.HEADER_TOP) + bars.top,
                JarvisUi.dp(topBar.getContext(), JarvisUi.PAGE_MARGIN) + bars.right,
                JarvisUi.dp(topBar.getContext(), JarvisUi.HEADER_BOTTOM)
            );
            navigationFrame.setPadding(
                JarvisUi.dp(navigationFrame.getContext(), JarvisUi.PAGE_MARGIN) + bars.left,
                0,
                JarvisUi.dp(navigationFrame.getContext(), JarvisUi.PAGE_MARGIN) + bars.right,
                0
            );
        }
    }

    private JarvisAppShell() {}

    static Header create(
        Activity activity,
        Destination destination,
        String modeLabel,
        String contextLabel,
        Actions actions
    ) {
        LinearLayout shell = new LinearLayout(activity);
        shell.setOrientation(LinearLayout.VERTICAL);
        shell.setBackgroundColor(JarvisUi.WHITE);
        shell.setContentDescription("Jarvis app header");

        LinearLayout topBar = new LinearLayout(activity);
        topBar.setOrientation(LinearLayout.HORIZONTAL);
        topBar.setGravity(Gravity.CENTER_VERTICAL);
        topBar.setBackgroundColor(JarvisUi.WHITE);
        topBar.setPadding(
            JarvisUi.dp(activity, JarvisUi.PAGE_MARGIN),
            JarvisUi.dp(activity, JarvisUi.HEADER_TOP),
            JarvisUi.dp(activity, JarvisUi.PAGE_MARGIN),
            JarvisUi.dp(activity, JarvisUi.HEADER_BOTTOM)
        );

        LinearLayout titleBlock = new LinearLayout(activity);
        titleBlock.setOrientation(LinearLayout.VERTICAL);
        TextView title = JarvisUi.text(activity, "J A R V I S", 18, JarvisUi.BLACK);
        title.setTypeface(Typeface.create("sans-serif-medium", Typeface.NORMAL));
        title.setLetterSpacing(0.12f);
        titleBlock.addView(title, matchWrap());
        TextView mode = JarvisUi.text(activity, modeLabel, 13, JarvisUi.BLACK);
        mode.setTypeface(Typeface.create("sans-serif-medium", Typeface.NORMAL));
        mode.setPadding(0, JarvisUi.dp(activity, 3), JarvisUi.dp(activity, 12), JarvisUi.dp(activity, 2));
        mode.setContentDescription("Assistant selector, " + modeLabel.replace("⌄", "").trim());
        mode.setOnClickListener(view -> actions.onMode());
        titleBlock.addView(mode, matchWrap());
        TextView context = JarvisUi.text(activity, contextLabel, 12, JarvisUi.MID);
        context.setMaxLines(1);
        context.setPadding(0, JarvisUi.dp(activity, 2), JarvisUi.dp(activity, 8), 0);
        titleBlock.addView(context, matchWrap());
        topBar.addView(titleBlock, new LinearLayout.LayoutParams(
            0,
            ViewGroup.LayoutParams.WRAP_CONTENT,
            1f
        ));

        ImageButton notifications = action(
            activity, R.drawable.ic_notifications, "House activity", JarvisUi.BLACK, actions::onNotifications
        );
        topBar.addView(notifications, iconParams(activity, 0));
        ImageButton newChat = action(
            activity, R.drawable.ic_add, "New chat", JarvisUi.BLACK, actions::onNewChat
        );
        topBar.addView(newChat, iconParams(activity, 0));
        ImageButton clearChat = action(
            activity, R.drawable.ic_delete, "Clear current chat", JarvisUi.DANGER, actions::onClearChat
        );
        topBar.addView(clearChat, iconParams(activity, 0));
        ImageButton settings = action(
            activity, R.drawable.ic_settings, "Settings", JarvisUi.BLACK, actions::onSettings
        );
        topBar.addView(settings, iconParams(activity, 0));
        shell.addView(topBar, matchWrap());

        LinearLayout navigationFrame = new LinearLayout(activity);
        navigationFrame.setOrientation(LinearLayout.HORIZONTAL);
        navigationFrame.setContentDescription("Primary navigation");
        navigationFrame.setPadding(
            JarvisUi.dp(activity, JarvisUi.PAGE_MARGIN),
            0,
            JarvisUi.dp(activity, JarvisUi.PAGE_MARGIN),
            0
        );
        LinearLayout navigation = new LinearLayout(activity);
        navigation.setOrientation(LinearLayout.HORIZONTAL);
        navigation.setPadding(
            JarvisUi.dp(activity, 3),
            JarvisUi.dp(activity, 3),
            JarvisUi.dp(activity, 3),
            JarvisUi.dp(activity, 3)
        );
        navigation.setBackground(JarvisUi.rounded(
            activity, JarvisUi.SOFT, JarvisUi.RADIUS_LARGE, 1, JarvisUi.LINE
        ));
        TextView home = JarvisUi.segmentedItem(activity, "Home", destination == Destination.HOME);
        home.setOnClickListener(view -> actions.onHome());
        navigation.addView(home, new LinearLayout.LayoutParams(
            0,
            JarvisUi.dp(activity, JarvisUi.PRIMARY_NAV_HEIGHT - 6),
            1f
        ));
        TextView chat = JarvisUi.segmentedItem(activity, "Chat", destination == Destination.CHAT);
        chat.setOnClickListener(view -> actions.onChat());
        navigation.addView(chat, new LinearLayout.LayoutParams(
            0,
            JarvisUi.dp(activity, JarvisUi.PRIMARY_NAV_HEIGHT - 6),
            1f
        ));
        TextView tasks = JarvisUi.segmentedItem(activity, "Tasks", destination == Destination.TASKS);
        tasks.setOnClickListener(view -> actions.onTasks());
        navigation.addView(tasks, new LinearLayout.LayoutParams(
            0,
            JarvisUi.dp(activity, JarvisUi.PRIMARY_NAV_HEIGHT - 6),
            1f
        ));
        navigationFrame.addView(navigation, new LinearLayout.LayoutParams(
            ViewGroup.LayoutParams.MATCH_PARENT,
            JarvisUi.dp(activity, JarvisUi.PRIMARY_NAV_HEIGHT)
        ));
        shell.addView(navigationFrame, matchWrap(JarvisUi.dp(activity, JarvisUi.SPACE_4), JarvisUi.dp(activity, JarvisUi.SPACE_8)));

        return new Header(
            shell,
            topBar,
            navigationFrame,
            mode,
            context,
            home,
            chat,
            tasks,
            notifications,
            newChat,
            clearChat,
            settings
        );
    }

    private static ImageButton action(
        Activity activity,
        int icon,
        String description,
        int foreground,
        Runnable action
    ) {
        ImageButton button = JarvisUi.actionButton(activity, icon, description, foreground);
        button.setOnClickListener(view -> action.run());
        return button;
    }

    private static LinearLayout.LayoutParams iconParams(Activity activity, int marginEnd) {
        LinearLayout.LayoutParams params = new LinearLayout.LayoutParams(
            JarvisUi.dp(activity, JarvisUi.TOUCH_TARGET),
            JarvisUi.dp(activity, JarvisUi.TOUCH_TARGET)
        );
        params.setMarginEnd(JarvisUi.dp(activity, marginEnd));
        return params;
    }

    private static LinearLayout.LayoutParams matchWrap() {
        return matchWrap(0, 0);
    }

    private static LinearLayout.LayoutParams matchWrap(int top, int bottom) {
        LinearLayout.LayoutParams params = new LinearLayout.LayoutParams(
            ViewGroup.LayoutParams.MATCH_PARENT,
            ViewGroup.LayoutParams.WRAP_CONTENT
        );
        params.topMargin = top;
        params.bottomMargin = bottom;
        return params;
    }
}
