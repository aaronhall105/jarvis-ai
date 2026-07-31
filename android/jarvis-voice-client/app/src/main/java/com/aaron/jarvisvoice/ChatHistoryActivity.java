package com.aaron.jarvisvoice;

import android.app.Activity;
import android.app.AlertDialog;
import android.content.Intent;
import android.graphics.Color;
import android.graphics.Insets;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.os.Bundle;
import android.text.Editable;
import android.text.TextWatcher;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.view.WindowInsets;
import android.widget.Button;
import android.widget.EditText;
import android.widget.ImageButton;
import android.widget.LinearLayout;
import android.widget.PopupMenu;
import android.widget.ScrollView;
import android.widget.TextView;
import android.widget.Toast;

import java.text.DateFormat;
import java.time.LocalDate;
import java.time.ZoneId;
import java.util.List;

public final class ChatHistoryActivity extends Activity {
    private static final int BLACK = Color.rgb(20, 20, 20);
    private static final int MID = Color.rgb(103, 103, 103);
    private static final int LINE = Color.rgb(226, 226, 226);
    private static final int SOFT = Color.rgb(246, 246, 246);
    private static final int WHITE = Color.WHITE;

    private ChatHistoryStore history;
    private SecureStore store;
    private EditText search;
    private LinearLayout list;

    @Override protected void onCreate(Bundle state) {
        super.onCreate(state);
        history = new ChatHistoryStore(this);
        store = new SecureStore(this);
        setContentView(buildContent());
    }

    @Override protected void onResume() {
        super.onResume();
        render();
    }

    private View buildContent() {
        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setBackgroundColor(WHITE);

        LinearLayout top = new LinearLayout(this);
        top.setOrientation(LinearLayout.HORIZONTAL);
        top.setGravity(Gravity.CENTER_VERTICAL);

        ImageButton back = iconButton(
            R.drawable.ic_back,
            "Back"
        );
        back.setOnClickListener(view -> finish());
        top.addView(
            back,
            new LinearLayout.LayoutParams(dp(42), dp(42))
        );

        LinearLayout titleBlock = new LinearLayout(this);
        titleBlock.setOrientation(LinearLayout.VERTICAL);
        titleBlock.setPadding(dp(12), 0, 0, 0);
        TextView title = text("Chats", 24, BLACK);
        title.setTypeface(
            Typeface.create(
                "sans-serif-medium",
                Typeface.NORMAL
            )
        );
        titleBlock.addView(title, wrap());
        TextView owner = text(
            "Conversations for " + store.userName(),
            12,
            MID
        );
        titleBlock.addView(owner, wrap());
        top.addView(
            titleBlock,
            new LinearLayout.LayoutParams(
                0,
                ViewGroup.LayoutParams.WRAP_CONTENT,
                1f
            )
        );

        Button newChat = button("New chat");
        newChat.setOnClickListener(view -> createNewChat());
        top.addView(
            newChat,
            new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT,
                dp(44)
            )
        );

        root.addView(top, matchWrap());

        search = new EditText(this);
        search.setHint("Search chats");
        search.setSingleLine(true);
        search.setTextSize(15);
        search.setTextColor(BLACK);
        search.setHintTextColor(MID);
        search.setPadding(dp(14), dp(10), dp(14), dp(10));
        search.setBackground(rounded(SOFT, 16, 1, LINE));
        search.addTextChangedListener(new TextWatcher() {
            @Override public void beforeTextChanged(
                CharSequence value,
                int start,
                int count,
                int after
            ) {}

            @Override public void onTextChanged(
                CharSequence value,
                int start,
                int before,
                int count
            ) {
                render();
            }

            @Override public void afterTextChanged(
                Editable value
            ) {}
        });
        LinearLayout.LayoutParams searchParams = matchWrap();
        searchParams.setMargins(dp(16), dp(10), dp(16), dp(8));
        root.addView(search, searchParams);

        ScrollView scroll = new ScrollView(this);
        scroll.setFillViewport(true);
        list = new LinearLayout(this);
        list.setOrientation(LinearLayout.VERTICAL);
        list.setPadding(dp(16), dp(4), dp(16), dp(30));
        scroll.addView(
            list,
            new ScrollView.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT
            )
        );
        root.addView(
            scroll,
            new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                0,
                1f
            )
        );

        root.setOnApplyWindowInsetsListener((view, insets) -> {
            Insets bars = insets.getInsets(
                WindowInsets.Type.systemBars()
            );
            top.setPadding(
                dp(10) + bars.left,
                dp(8) + bars.top,
                dp(12) + bars.right,
                dp(8)
            );
            scroll.setPadding(
                bars.left,
                0,
                bars.right,
                bars.bottom
            );
            return insets;
        });
        root.requestApplyInsets();
        return root;
    }

    private void render() {
        if (list == null || history == null) return;
        list.removeAllViews();

        String query = search == null
            ? ""
            : search.getText().toString();
        List<ChatConversation> conversations =
            history.conversations(query);

        if (conversations.isEmpty()) {
            TextView empty = text(
                query.isBlank()
                    ? "No saved chats yet."
                    : "No chats match your search.",
                15,
                MID
            );
            empty.setGravity(Gravity.CENTER);
            empty.setPadding(0, dp(70), 0, 0);
            list.addView(empty, matchWrap());
            return;
        }

        ZoneId zone = ZoneId.systemDefault();
        long today = LocalDate.now(zone)
            .atStartOfDay(zone)
            .toInstant()
            .toEpochMilli();
        long sevenDays = LocalDate.now(zone)
            .minusDays(7)
            .atStartOfDay(zone)
            .toInstant()
            .toEpochMilli();

        String lastSection = "";
        for (ChatConversation conversation : conversations) {
            String section = ConversationPolicy.section(
                conversation.updatedAt,
                today,
                sevenDays
            );
            if (!section.equals(lastSection)) {
                TextView heading = text(section, 14, MID);
                heading.setTypeface(Typeface.DEFAULT_BOLD);
                heading.setPadding(
                    dp(2),
                    lastSection.isBlank() ? dp(8) : dp(20),
                    0,
                    dp(8)
                );
                list.addView(heading, matchWrap());
                lastSection = section;
            }
            list.addView(
                conversationRow(conversation),
                matchWrap(0, dp(8))
            );
        }
    }

    private View conversationRow(
        ChatConversation conversation
    ) {
        LinearLayout row = new LinearLayout(this);
        row.setOrientation(LinearLayout.HORIZONTAL);
        row.setGravity(Gravity.CENTER_VERTICAL);
        row.setPadding(dp(14), dp(12), dp(8), dp(12));
        row.setBackground(rounded(WHITE, 18, 1, LINE));

        LinearLayout copy = new LinearLayout(this);
        copy.setOrientation(LinearLayout.VERTICAL);

        LinearLayout titleLine = new LinearLayout(this);
        titleLine.setOrientation(LinearLayout.HORIZONTAL);
        titleLine.setGravity(Gravity.CENTER_VERTICAL);
        TextView title = text(
            conversation.title,
            16,
            BLACK
        );
        title.setTypeface(
            Typeface.create(
                "sans-serif-medium",
                Typeface.NORMAL
            )
        );
        title.setMaxLines(1);
        titleLine.addView(
            title,
            new LinearLayout.LayoutParams(
                0,
                ViewGroup.LayoutParams.WRAP_CONTENT,
                1f
            )
        );
        if (conversation.pinned) {
            TextView pin = text("Pinned", 11, MID);
            pin.setPadding(dp(8), dp(2), dp(8), dp(2));
            pin.setBackground(rounded(
                SOFT,
                10,
                0,
                Color.TRANSPARENT
            ));
            titleLine.addView(pin, wrap());
        }
        copy.addView(titleLine, matchWrap());

        String preview = conversation.preview.isBlank()
            ? "Empty conversation"
            : conversation.preview;
        TextView note = text(preview, 13, MID);
        note.setMaxLines(2);
        note.setPadding(0, dp(4), dp(8), 0);
        copy.addView(note, matchWrap());

        String date = DateFormat.getDateTimeInstance(
            DateFormat.SHORT,
            DateFormat.SHORT
        ).format(conversation.updatedAt);
        TextView metadata = text(
            conversation.messageCount + " messages · " + date,
            11,
            MID
        );
        metadata.setPadding(0, dp(5), 0, 0);
        copy.addView(metadata, matchWrap());

        row.addView(
            copy,
            new LinearLayout.LayoutParams(
                0,
                ViewGroup.LayoutParams.WRAP_CONTENT,
                1f
            )
        );

        ImageButton menu = iconButton(
            R.drawable.ic_settings,
            "Chat options"
        );
        menu.setOnClickListener(view ->
            showOptions(view, conversation)
        );
        row.addView(
            menu,
            new LinearLayout.LayoutParams(dp(40), dp(40))
        );

        row.setOnClickListener(view ->
            openConversation(conversation.id)
        );
        row.setOnLongClickListener(view -> {
            showOptions(view, conversation);
            return true;
        });
        return row;
    }

    private void showOptions(
        View anchor,
        ChatConversation conversation
    ) {
        PopupMenu popup = new PopupMenu(this, anchor);
        popup.getMenu().add(0, 1, 0, "Rename");
        popup.getMenu().add(
            0,
            2,
            1,
            conversation.pinned ? "Unpin" : "Pin"
        );
        popup.getMenu().add(0, 3, 2, "Delete");
        popup.setOnMenuItemClickListener(item -> {
            return switch (item.getItemId()) {
                case 1 -> {
                    rename(conversation);
                    yield true;
                }
                case 2 -> {
                    history.togglePinned(conversation.id);
                    render();
                    yield true;
                }
                case 3 -> {
                    confirmDelete(conversation);
                    yield true;
                }
                default -> false;
            };
        });
        popup.show();
    }

    private void rename(ChatConversation conversation) {
        EditText value = new EditText(this);
        value.setSingleLine(true);
        value.setText(conversation.title);
        value.setSelection(value.length());
        new AlertDialog.Builder(this)
            .setTitle("Rename chat")
            .setView(value)
            .setPositiveButton("Save", (dialog, which) -> {
                history.renameConversation(
                    conversation.id,
                    value.getText().toString()
                );
                render();
            })
            .setNegativeButton("Cancel", null)
            .show();
    }

    private void confirmDelete(
        ChatConversation conversation
    ) {
        new AlertDialog.Builder(this)
            .setTitle("Delete chat?")
            .setMessage(
                "This removes the local chat history. "
                    + "It cannot be undone."
            )
            .setPositiveButton("Delete", (dialog, which) -> {
                boolean wasActive = conversation.id.equals(
                    history.activeConversationId()
                );
                history.deleteConversation(conversation.id);
                if (wasActive) {
                    reconnectActiveConversation();
                }
                render();
            })
            .setNegativeButton("Cancel", null)
            .show();
    }

    private void createNewChat() {
        String id = history.createConversation();
        openConversation(id);
    }

    private void openConversation(String id) {
        if (!history.switchConversation(id)) {
            Toast.makeText(
                this,
                "This chat belongs to another profile",
                Toast.LENGTH_LONG
            ).show();
            return;
        }
        startForegroundService(
            new Intent(this, VoiceService.class)
                .setAction(VoiceService.ACTION_SWITCH_CHAT)
                .putExtra(
                    VoiceService.EXTRA_CONVERSATION_ID,
                    id
                )
        );
        finish();
    }

    private void reconnectActiveConversation() {
        String id = history.activeConversationId();
        startForegroundService(
            new Intent(this, VoiceService.class)
                .setAction(VoiceService.ACTION_SWITCH_CHAT)
                .putExtra(
                    VoiceService.EXTRA_CONVERSATION_ID,
                    id
                )
        );
    }

    private ImageButton iconButton(
        int icon,
        String description
    ) {
        ImageButton value = new ImageButton(this);
        value.setImageResource(icon);
        value.setColorFilter(BLACK);
        value.setContentDescription(description);
        value.setPadding(dp(10), dp(10), dp(10), dp(10));
        value.setBackground(rounded(
            SOFT,
            21,
            0,
            Color.TRANSPARENT
        ));
        return value;
    }

    private Button button(String label) {
        Button value = new Button(this);
        value.setText(label);
        value.setAllCaps(false);
        value.setTextSize(14);
        value.setTextColor(WHITE);
        value.setPadding(dp(14), 0, dp(14), 0);
        value.setBackground(rounded(
            BLACK,
            16,
            0,
            Color.TRANSPARENT
        ));
        return value;
    }

    private TextView text(
        String value,
        int size,
        int colour
    ) {
        TextView view = new TextView(this);
        view.setText(value);
        view.setTextSize(size);
        view.setTextColor(colour);
        view.setTypeface(
            Typeface.create(
                "sans-serif",
                Typeface.NORMAL
            )
        );
        return view;
    }

    private GradientDrawable rounded(
        int colour,
        int radius,
        int strokeWidth,
        int strokeColour
    ) {
        GradientDrawable drawable = new GradientDrawable();
        drawable.setColor(colour);
        drawable.setCornerRadius(dp(radius));
        if (strokeWidth > 0) {
            drawable.setStroke(dp(strokeWidth), strokeColour);
        }
        return drawable;
    }

    private LinearLayout.LayoutParams matchWrap() {
        return new LinearLayout.LayoutParams(
            ViewGroup.LayoutParams.MATCH_PARENT,
            ViewGroup.LayoutParams.WRAP_CONTENT
        );
    }

    private LinearLayout.LayoutParams matchWrap(
        int top,
        int bottom
    ) {
        LinearLayout.LayoutParams params = matchWrap();
        params.setMargins(0, top, 0, bottom);
        return params;
    }

    private LinearLayout.LayoutParams wrap() {
        return new LinearLayout.LayoutParams(
            ViewGroup.LayoutParams.WRAP_CONTENT,
            ViewGroup.LayoutParams.WRAP_CONTENT
        );
    }

    private int dp(int value) {
        return Math.round(
            value * getResources()
                .getDisplayMetrics()
                .density
        );
    }
}
