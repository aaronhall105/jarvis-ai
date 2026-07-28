package com.aaron.jarvisvoice;

import android.Manifest;
import android.app.Activity;
import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.content.IntentFilter;
import android.content.pm.PackageManager;
import android.graphics.Color;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.os.Bundle;
import android.text.InputType;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.view.Window;
import android.widget.Button;
import android.widget.EditText;
import android.widget.FrameLayout;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;
import android.widget.Toast;

import java.util.ArrayList;
import java.util.List;

public final class MainActivity extends Activity {
    private static final int BLACK = Color.rgb(20, 20, 20);
    private static final int MID = Color.rgb(105, 105, 105);
    private static final int LINE = Color.rgb(225, 225, 225);
    private static final int SOFT = Color.rgb(245, 245, 245);
    private static final int WHITE = Color.WHITE;

    private SecureStore store;
    private ChatHistoryStore history;
    private ScrollView messageScroll;
    private LinearLayout messageList;
    private EditText composer;
    private TextView statusText;
    private Button voiceButton;
    private Button modeButton;
    private TextView emptyState;
    private TextView streamingText;
    private boolean voiceActive;
    private boolean listening;

    private final BroadcastReceiver receiver = new BroadcastReceiver() {
        @Override public void onReceive(Context context, Intent intent) {
            if (!VoiceService.ACTION_EVENT.equals(intent.getAction())) return;
            String event = safe(intent.getStringExtra(VoiceService.EXTRA_EVENT));
            String role = safe(intent.getStringExtra(VoiceService.EXTRA_ROLE));
            String text = safe(intent.getStringExtra(VoiceService.EXTRA_TEXT));
            voiceActive = intent.getBooleanExtra(VoiceService.EXTRA_ACTIVE, voiceActive);
            listening = intent.getBooleanExtra(VoiceService.EXTRA_LISTENING, listening);
            updateModeButton();
            updateVoiceButton();
            switch (event) {
                case "status" -> statusText.setText(text);
                case "message" -> {
                    finishStreaming();
                    addMessageView(new ChatMessage(role, text, System.currentTimeMillis()), true);
                }
                case "assistant_delta" -> appendStreaming(text);
                case "thinking" -> beginStreaming();
                case "draft" -> statusText.setText(text.isBlank() ? "Listening" : "Listening: " + text);
                case "state" -> { }
                case "clear" -> renderHistory();
                case "error" -> Toast.makeText(MainActivity.this, text, Toast.LENGTH_LONG).show();
                default -> { }
            }
        }
    };

    @Override protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        store = new SecureStore(this);
        history = new ChatHistoryStore(this);
        configureWindow();
        setContentView(buildContent());
        renderHistory();
        updateModeButton();
        updateVoiceButton();
        if (store.hasMobileToken() &&
            checkSelfPermission(Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED) {
            startForegroundService(new Intent(this, VoiceService.class).setAction(VoiceService.ACTION_START));
        }
    }

    @Override protected void onStart() {
        super.onStart();
        IntentFilter filter = new IntentFilter(VoiceService.ACTION_EVENT);
        if (android.os.Build.VERSION.SDK_INT >= 33) {
            registerReceiver(receiver, filter, Context.RECEIVER_NOT_EXPORTED);
        } else {
            registerReceiver(receiver, filter);
        }
        renderHistory();
        updateModeButton();
    }

    @Override protected void onStop() {
        try { unregisterReceiver(receiver); } catch (Exception ignored) {}
        super.onStop();
    }

    @Override protected void onResume() {
        super.onResume();
        updateModeButton();
    }

    private void configureWindow() {
        Window window = getWindow();
        window.setStatusBarColor(WHITE);
        window.setNavigationBarColor(WHITE);
        window.getDecorView().setSystemUiVisibility(
            View.SYSTEM_UI_FLAG_LIGHT_STATUS_BAR | View.SYSTEM_UI_FLAG_LIGHT_NAVIGATION_BAR
        );
    }

    private View buildContent() {
        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setBackgroundColor(WHITE);

        root.addView(buildTopBar(), new LinearLayout.LayoutParams(
            ViewGroup.LayoutParams.MATCH_PARENT,
            dp(58)
        ));

        statusText = text("Connecting", 12, MID);
        statusText.setGravity(Gravity.CENTER_HORIZONTAL);
        statusText.setPadding(dp(16), dp(5), dp(16), dp(7));
        root.addView(statusText, matchWrap());

        FrameLayout conversation = new FrameLayout(this);
        messageScroll = new ScrollView(this);
        messageScroll.setFillViewport(true);
        messageScroll.setClipToPadding(false);
        messageScroll.setPadding(dp(14), dp(8), dp(14), dp(12));
        messageList = new LinearLayout(this);
        messageList.setOrientation(LinearLayout.VERTICAL);
        messageList.setPadding(0, 0, 0, dp(16));
        messageScroll.addView(messageList, new ScrollView.LayoutParams(
            ViewGroup.LayoutParams.MATCH_PARENT,
            ViewGroup.LayoutParams.WRAP_CONTENT
        ));
        conversation.addView(messageScroll, new FrameLayout.LayoutParams(
            ViewGroup.LayoutParams.MATCH_PARENT,
            ViewGroup.LayoutParams.MATCH_PARENT
        ));

        emptyState = text("How can I help?", 28, BLACK);
        emptyState.setTypeface(Typeface.create("sans-serif", Typeface.NORMAL));
        emptyState.setGravity(Gravity.CENTER);
        FrameLayout.LayoutParams emptyParams = new FrameLayout.LayoutParams(
            ViewGroup.LayoutParams.MATCH_PARENT,
            ViewGroup.LayoutParams.WRAP_CONTENT,
            Gravity.CENTER
        );
        emptyParams.setMargins(dp(24), 0, dp(24), dp(80));
        conversation.addView(emptyState, emptyParams);

        LinearLayout.LayoutParams conversationParams = new LinearLayout.LayoutParams(
            ViewGroup.LayoutParams.MATCH_PARENT,
            0,
            1f
        );
        root.addView(conversation, conversationParams);
        root.addView(buildComposer(), new LinearLayout.LayoutParams(
            ViewGroup.LayoutParams.MATCH_PARENT,
            ViewGroup.LayoutParams.WRAP_CONTENT
        ));
        return root;
    }

    private View buildTopBar() {
        LinearLayout bar = new LinearLayout(this);
        bar.setOrientation(LinearLayout.HORIZONTAL);
        bar.setGravity(Gravity.CENTER_VERTICAL);
        bar.setPadding(dp(16), dp(4), dp(10), dp(4));
        bar.setBackgroundColor(WHITE);

        TextView title = text("Jarvis", 22, BLACK);
        title.setTypeface(Typeface.create("sans-serif-medium", Typeface.NORMAL));
        bar.addView(title, new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f));

        modeButton = smallButton("Live");
        modeButton.setOnClickListener(view -> toggleMode());
        bar.addView(modeButton, wrapWrap(0, dp(6)));

        Button newChat = topTextButton("New");
        newChat.setOnClickListener(view -> newChat());
        bar.addView(newChat, wrapWrap(0, dp(2)));

        Button settings = topTextButton("Settings");
        settings.setOnClickListener(view -> startActivity(new Intent(this, SettingsActivity.class)));
        bar.addView(settings, wrapWrap());
        return bar;
    }

    private View buildComposer() {
        LinearLayout wrapper = new LinearLayout(this);
        wrapper.setOrientation(LinearLayout.VERTICAL);
        wrapper.setPadding(dp(12), dp(6), dp(12), dp(12));
        wrapper.setBackgroundColor(WHITE);

        LinearLayout row = new LinearLayout(this);
        row.setOrientation(LinearLayout.HORIZONTAL);
        row.setGravity(Gravity.BOTTOM);
        row.setPadding(dp(8), dp(7), dp(7), dp(7));
        row.setBackground(rounded(SOFT, 24, 0, Color.TRANSPARENT));

        composer = new EditText(this);
        composer.setHint("Message Jarvis");
        composer.setHintTextColor(Color.rgb(130, 130, 130));
        composer.setTextColor(BLACK);
        composer.setTextSize(16);
        composer.setBackgroundColor(Color.TRANSPARENT);
        composer.setPadding(dp(6), dp(4), dp(8), dp(4));
        composer.setMinHeight(dp(42));
        composer.setMaxLines(5);
        composer.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_FLAG_CAP_SENTENCES | InputType.TYPE_TEXT_FLAG_MULTI_LINE);
        row.addView(composer, new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f));

        Button send = circleButton("↑", BLACK, WHITE);
        send.setOnClickListener(view -> sendMessage());
        row.addView(send, new LinearLayout.LayoutParams(dp(42), dp(42)));
        wrapper.addView(row, matchWrap());

        voiceButton = button("Start voice");
        voiceButton.setOnClickListener(view -> toggleVoice());
        LinearLayout.LayoutParams voiceParams = matchWrap(dp(8), 0);
        wrapper.addView(voiceButton, voiceParams);
        return wrapper;
    }

    private void sendMessage() {
        String text = composer.getText().toString().trim();
        if (text.isEmpty()) return;
        if (!credentialsReady()) return;
        composer.setText("");
        Intent intent = new Intent(this, VoiceService.class)
            .setAction(VoiceService.ACTION_SEND_TEXT)
            .putExtra(VoiceService.EXTRA_TEXT, text);
        startForegroundService(intent);
    }

    private void toggleVoice() {
        if (voiceActive) {
            startService(new Intent(this, VoiceService.class).setAction(VoiceService.ACTION_STOP_VOICE));
            return;
        }
        if (!credentialsReady()) return;
        List<String> missing = new ArrayList<>();
        if (checkSelfPermission(Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) {
            missing.add(Manifest.permission.RECORD_AUDIO);
        }
        if (android.os.Build.VERSION.SDK_INT >= 33 &&
            checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) {
            missing.add(Manifest.permission.POST_NOTIFICATIONS);
        }
        if (!missing.isEmpty()) {
            requestPermissions(missing.toArray(new String[0]), 1800);
            return;
        }
        startForegroundService(new Intent(this, VoiceService.class).setAction(VoiceService.ACTION_START_VOICE));
    }

    @Override public void onRequestPermissionsResult(int requestCode, String[] permissions, int[] grantResults) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults);
        if (requestCode != 1800) return;
        for (int result : grantResults) {
            if (result != PackageManager.PERMISSION_GRANTED) {
                Toast.makeText(this, "Microphone permission is required for voice", Toast.LENGTH_LONG).show();
                return;
            }
        }
        toggleVoice();
    }

    private boolean credentialsReady() {
        if (store.coreUrl().isBlank() || store.mobileToken().isBlank()) {
            Toast.makeText(this, "Open Settings and add the Jarvis Core URL and mobile token", Toast.LENGTH_LONG).show();
            startActivity(new Intent(this, SettingsActivity.class));
            return false;
        }
        if (VoiceCatalog.isOriginal(store.voiceId()) &&
            (store.homeAssistantUrl().isBlank() || store.homeAssistantToken().isBlank())) {
            Toast.makeText(this, "The original Jarvis voice also needs Home Assistant details", Toast.LENGTH_LONG).show();
            startActivity(new Intent(this, SettingsActivity.class));
            return false;
        }
        return true;
    }

    private void toggleMode() {
        String next = ConversationMode.toggle(store.conversationMode());
        store.setConversationMode(next);
        voiceActive = false;
        listening = false;
        updateModeButton();
        updateVoiceButton();
        startForegroundService(new Intent(this, VoiceService.class).setAction(VoiceService.ACTION_APPLY_SETTINGS));
        Toast.makeText(this, ConversationMode.label(next) + " voice selected", Toast.LENGTH_SHORT).show();
    }

    private void newChat() {
        history.clear();
        store.newConversationId();
        renderHistory();
        startForegroundService(new Intent(this, VoiceService.class).setAction(VoiceService.ACTION_NEW_CHAT));
    }

    private void renderHistory() {
        if (messageList == null) return;
        messageList.removeAllViews();
        streamingText = null;
        List<ChatMessage> messages = history.list();
        emptyState.setVisibility(messages.isEmpty() ? View.VISIBLE : View.GONE);
        for (ChatMessage message : messages) addMessageView(message, false);
        scrollToBottom();
    }

    private void addMessageView(ChatMessage message, boolean scroll) {
        if (message.text.isBlank()) return;
        emptyState.setVisibility(View.GONE);
        LinearLayout holder = new LinearLayout(this);
        holder.setOrientation(LinearLayout.VERTICAL);
        boolean user = ChatMessage.USER.equals(message.role);
        boolean assistant = ChatMessage.ASSISTANT.equals(message.role);
        holder.setGravity(user ? Gravity.END : Gravity.START);

        if (assistant) {
            TextView name = text("Jarvis", 12, MID);
            name.setTypeface(Typeface.DEFAULT_BOLD);
            holder.addView(name, wrapWrap(0, dp(4)));
        }

        TextView content = text(message.text, 16, BLACK);
        content.setTextIsSelectable(true);
        content.setLineSpacing(0f, 1.12f);
        content.setPadding(user ? dp(14) : 0, dp(10), user ? dp(14) : dp(8), dp(10));
        if (user) content.setBackground(rounded(SOFT, 18, 0, Color.TRANSPARENT));
        LinearLayout.LayoutParams contentParams = new LinearLayout.LayoutParams(
            ViewGroup.LayoutParams.WRAP_CONTENT,
            ViewGroup.LayoutParams.WRAP_CONTENT
        );
        contentParams.width = user ? Math.min(getResources().getDisplayMetrics().widthPixels - dp(80), dp(520)) : ViewGroup.LayoutParams.WRAP_CONTENT;
        holder.addView(content, contentParams);

        LinearLayout.LayoutParams holderParams = new LinearLayout.LayoutParams(
            ViewGroup.LayoutParams.MATCH_PARENT,
            ViewGroup.LayoutParams.WRAP_CONTENT
        );
        holderParams.setMargins(dp(4), dp(7), dp(4), dp(11));
        messageList.addView(holder, holderParams);
        if (scroll) scrollToBottom();
    }

    private void beginStreaming() {
        if (streamingText != null) return;
        emptyState.setVisibility(View.GONE);
        LinearLayout holder = new LinearLayout(this);
        holder.setOrientation(LinearLayout.VERTICAL);
        holder.setGravity(Gravity.START);
        TextView name = text("Jarvis", 12, MID);
        name.setTypeface(Typeface.DEFAULT_BOLD);
        holder.addView(name, wrapWrap(0, dp(4)));
        streamingText = text("Thinking…", 16, MID);
        streamingText.setLineSpacing(0f, 1.12f);
        streamingText.setPadding(0, dp(10), dp(8), dp(10));
        holder.addView(streamingText, wrapWrap());
        LinearLayout.LayoutParams params = matchWrap(dp(7), dp(11));
        messageList.addView(holder, params);
        scrollToBottom();
    }

    private void appendStreaming(String delta) {
        if (delta == null || delta.isEmpty()) return;
        if (streamingText == null) beginStreaming();
        String current = streamingText.getText().toString();
        if ("Thinking…".equals(current)) current = "";
        streamingText.setText(current + delta);
        streamingText.setTextColor(BLACK);
        scrollToBottom();
    }

    private void finishStreaming() {
        if (streamingText == null) return;
        View parent = (View) streamingText.getParent();
        if (parent != null && parent.getParent() == messageList) messageList.removeView(parent);
        streamingText = null;
    }

    private void scrollToBottom() {
        messageScroll.post(() -> messageScroll.fullScroll(View.FOCUS_DOWN));
    }

    private void updateVoiceButton() {
        if (voiceButton == null) return;
        if (voiceActive) {
            voiceButton.setText(listening ? "End voice · Listening" : "End voice");
            voiceButton.setTextColor(WHITE);
            voiceButton.setBackground(rounded(BLACK, 22, 0, Color.TRANSPARENT));
        } else {
            voiceButton.setText("Start " + ConversationMode.label(store.conversationMode()) + " voice");
            voiceButton.setTextColor(BLACK);
            voiceButton.setBackground(rounded(WHITE, 22, 1, LINE));
        }
    }

    private void updateModeButton() {
        if (modeButton == null) return;
        modeButton.setText(ConversationMode.label(store.conversationMode()));
    }

    private Button topTextButton(String label) {
        Button value = new Button(this);
        value.setText(label);
        value.setTextSize(13);
        value.setTextColor(BLACK);
        value.setAllCaps(false);
        value.setMinWidth(0);
        value.setMinimumWidth(0);
        value.setMinHeight(0);
        value.setMinimumHeight(0);
        value.setPadding(dp(8), dp(6), dp(8), dp(6));
        value.setBackgroundColor(Color.TRANSPARENT);
        return value;
    }

    private Button smallButton(String label) {
        Button value = topTextButton(label);
        value.setPadding(dp(12), dp(6), dp(12), dp(6));
        value.setBackground(rounded(SOFT, 16, 0, Color.TRANSPARENT));
        return value;
    }

    private Button button(String label) {
        Button value = new Button(this);
        value.setText(label);
        value.setTextSize(15);
        value.setTextColor(BLACK);
        value.setAllCaps(false);
        value.setPadding(dp(16), dp(11), dp(16), dp(11));
        value.setBackground(rounded(WHITE, 22, 1, LINE));
        return value;
    }

    private Button circleButton(String label, int background, int foreground) {
        Button value = new Button(this);
        value.setText(label);
        value.setTextSize(22);
        value.setTextColor(foreground);
        value.setAllCaps(false);
        value.setPadding(0, 0, 0, dp(2));
        value.setMinWidth(0);
        value.setMinimumWidth(0);
        value.setMinHeight(0);
        value.setMinimumHeight(0);
        value.setGravity(Gravity.CENTER);
        value.setBackground(rounded(background, 21, 0, Color.TRANSPARENT));
        return value;
    }

    private TextView text(String value, int size, int colour) {
        TextView view = new TextView(this);
        view.setText(value);
        view.setTextSize(size);
        view.setTextColor(colour);
        view.setTypeface(Typeface.create("sans-serif", Typeface.NORMAL));
        return view;
    }

    private GradientDrawable rounded(int fill, int radiusDp, int strokeDp, int strokeColour) {
        GradientDrawable background = new GradientDrawable();
        background.setColor(fill);
        background.setCornerRadius(dp(radiusDp));
        if (strokeDp > 0) background.setStroke(dp(strokeDp), strokeColour);
        return background;
    }

    private LinearLayout.LayoutParams matchWrap() {
        return matchWrap(0, 0);
    }

    private LinearLayout.LayoutParams matchWrap(int top, int bottom) {
        LinearLayout.LayoutParams params = new LinearLayout.LayoutParams(
            ViewGroup.LayoutParams.MATCH_PARENT,
            ViewGroup.LayoutParams.WRAP_CONTENT
        );
        params.topMargin = top;
        params.bottomMargin = bottom;
        return params;
    }

    private LinearLayout.LayoutParams wrapWrap() {
        return wrapWrap(0, 0);
    }

    private LinearLayout.LayoutParams wrapWrap(int top, int right) {
        LinearLayout.LayoutParams params = new LinearLayout.LayoutParams(
            ViewGroup.LayoutParams.WRAP_CONTENT,
            ViewGroup.LayoutParams.WRAP_CONTENT
        );
        params.topMargin = top;
        params.rightMargin = right;
        return params;
    }

    private int dp(int value) {
        return Math.round(value * getResources().getDisplayMetrics().density);
    }

    private static String safe(String value) {
        return value == null ? "" : value;
    }
}
