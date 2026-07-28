package com.aaron.jarvisvoice;

import android.Manifest;
import android.app.Activity;
import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.content.IntentFilter;
import android.content.pm.PackageManager;
import android.graphics.Color;
import android.graphics.Insets;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.os.Bundle;
import android.text.Editable;
import android.text.InputType;
import android.text.TextWatcher;
import android.view.Gravity;
import android.view.KeyEvent;
import android.view.View;
import android.view.ViewGroup;
import android.view.Window;
import android.view.WindowInsets;
import android.view.WindowInsetsController;
import android.view.WindowManager;
import android.view.inputmethod.EditorInfo;
import android.widget.EditText;
import android.widget.FrameLayout;
import android.widget.ImageButton;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;
import android.widget.Toast;

import java.util.ArrayList;
import java.util.List;

public final class MainActivity extends Activity {
    private static final int BLACK = Color.rgb(20, 20, 20);
    private static final int MID = Color.rgb(103, 103, 103);
    private static final int LINE = Color.rgb(226, 226, 226);
    private static final int SOFT = Color.rgb(246, 246, 246);
    private static final int WHITE = Color.WHITE;
    private static final int REQUEST_VOICE_PERMISSIONS = 1800;

    private SecureStore store;
    private ChatHistoryStore history;
    private LinearLayout root;
    private LinearLayout topBar;
    private LinearLayout composerShell;
    private ScrollView messageScroll;
    private LinearLayout messageList;
    private EditText composer;
    private TextView statusText;
    private ImageButton micButton;
    private ImageButton sendButton;
    private View emptyState;
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
            updateMicButton();

            switch (event) {
                case "status" -> statusText.setText(text);
                case "message" -> {
                    finishStreaming();
                    addMessageView(new ChatMessage(role, text, System.currentTimeMillis()), true);
                }
                case "assistant_delta" -> appendStreaming(text);
                case "thinking" -> beginStreaming();
                case "draft" -> statusText.setText(text.isBlank() ? "Listening" : text);
                case "clear" -> renderHistory();
                case "error" -> {
                    statusText.setText("Something went wrong");
                    Toast.makeText(MainActivity.this, text, Toast.LENGTH_LONG).show();
                }
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
        applySystemInsets();
        renderHistory();
        updateMicButton();
        startJarvisIfConfigured();
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
    }

    @Override protected void onResume() {
        super.onResume();
        updateMicButton();
        JarvisVoiceInteractionService.refreshWakeIfActive(this);
    }

    @Override protected void onStop() {
        try { unregisterReceiver(receiver); } catch (Exception ignored) {}
        super.onStop();
    }

    private void configureWindow() {
        Window window = getWindow();
        window.setStatusBarColor(Color.TRANSPARENT);
        window.setNavigationBarColor(Color.TRANSPARENT);
        window.setNavigationBarDividerColor(Color.TRANSPARENT);
        window.setSoftInputMode(WindowManager.LayoutParams.SOFT_INPUT_ADJUST_RESIZE);

        WindowInsetsController controller = window.getInsetsController();
        if (controller != null) {
            int appearance =
                WindowInsetsController.APPEARANCE_LIGHT_STATUS_BARS
                    | WindowInsetsController.APPEARANCE_LIGHT_NAVIGATION_BARS;
            controller.setSystemBarsAppearance(appearance, appearance);
        }
    }

    private View buildContent() {
        root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setBackgroundColor(WHITE);

        topBar = buildTopBar();
        root.addView(topBar, matchWrap());

        FrameLayout conversation = new FrameLayout(this);
        messageScroll = new ScrollView(this);
        messageScroll.setFillViewport(true);
        messageScroll.setClipToPadding(false);
        messageList = new LinearLayout(this);
        messageList.setOrientation(LinearLayout.VERTICAL);
        messageList.setPadding(0, 0, 0, dp(18));
        messageScroll.addView(messageList, new ScrollView.LayoutParams(
            ViewGroup.LayoutParams.MATCH_PARENT,
            ViewGroup.LayoutParams.WRAP_CONTENT
        ));
        conversation.addView(messageScroll, new FrameLayout.LayoutParams(
            ViewGroup.LayoutParams.MATCH_PARENT,
            ViewGroup.LayoutParams.MATCH_PARENT
        ));

        LinearLayout welcome = new LinearLayout(this);
        welcome.setOrientation(LinearLayout.VERTICAL);
        welcome.setGravity(Gravity.CENTER);
        TextView welcomeTitle = text("What can I help with?", 27, BLACK);
        welcomeTitle.setTypeface(Typeface.create("sans-serif", Typeface.NORMAL));
        welcomeTitle.setGravity(Gravity.CENTER);
        welcome.addView(welcomeTitle, matchWrap());
        TextView welcomeNote = text("Type a message or tap the microphone.", 14, MID);
        welcomeNote.setGravity(Gravity.CENTER);
        welcomeNote.setPadding(0, dp(8), 0, 0);
        welcome.addView(welcomeNote, matchWrap());
        emptyState = welcome;

        FrameLayout.LayoutParams emptyParams = new FrameLayout.LayoutParams(
            ViewGroup.LayoutParams.MATCH_PARENT,
            ViewGroup.LayoutParams.WRAP_CONTENT,
            Gravity.CENTER
        );
        emptyParams.setMargins(dp(24), 0, dp(24), dp(76));
        conversation.addView(emptyState, emptyParams);

        root.addView(conversation, new LinearLayout.LayoutParams(
            ViewGroup.LayoutParams.MATCH_PARENT,
            0,
            1f
        ));

        composerShell = buildComposer();
        root.addView(composerShell, matchWrap());
        return root;
    }

    private LinearLayout buildTopBar() {
        LinearLayout bar = new LinearLayout(this);
        bar.setOrientation(LinearLayout.HORIZONTAL);
        bar.setGravity(Gravity.CENTER_VERTICAL);
        bar.setBackgroundColor(WHITE);

        LinearLayout titleBlock = new LinearLayout(this);
        titleBlock.setOrientation(LinearLayout.VERTICAL);
        TextView title = text("Jarvis", 22, BLACK);
        title.setTypeface(Typeface.create("sans-serif-medium", Typeface.NORMAL));
        titleBlock.addView(title, matchWrap());
        statusText = text("Connecting", 12, MID);
        statusText.setMaxLines(1);
        statusText.setPadding(0, dp(2), dp(8), 0);
        titleBlock.addView(statusText, matchWrap());
        bar.addView(titleBlock, new LinearLayout.LayoutParams(
            0,
            ViewGroup.LayoutParams.WRAP_CONTENT,
            1f
        ));

        ImageButton newChat = iconButton(R.drawable.ic_add, "New chat", SOFT, BLACK);
        newChat.setOnClickListener(view -> newChat());
        bar.addView(newChat, iconParams(dp(40), dp(6)));

        ImageButton settings = iconButton(R.drawable.ic_settings, "Settings", SOFT, BLACK);
        settings.setOnClickListener(view ->
            startActivity(new Intent(this, SettingsActivity.class))
        );
        bar.addView(settings, iconParams(dp(40), 0));
        return bar;
    }

    private LinearLayout buildComposer() {
        LinearLayout wrapper = new LinearLayout(this);
        wrapper.setOrientation(LinearLayout.VERTICAL);
        wrapper.setBackgroundColor(WHITE);

        LinearLayout row = new LinearLayout(this);
        row.setOrientation(LinearLayout.HORIZONTAL);
        row.setGravity(Gravity.CENTER_VERTICAL);
        row.setPadding(dp(12), dp(6), dp(6), dp(6));
        row.setBackground(rounded(SOFT, 25, 1, LINE));

        composer = new EditText(this);
        composer.setHint("Message Jarvis");
        composer.setHintTextColor(Color.rgb(132, 132, 132));
        composer.setTextColor(BLACK);
        composer.setTextSize(16);
        composer.setSingleLine(true);
        composer.setInputType(
            InputType.TYPE_CLASS_TEXT
                | InputType.TYPE_TEXT_FLAG_CAP_SENTENCES
                | InputType.TYPE_TEXT_FLAG_AUTO_CORRECT
        );
        composer.setImeOptions(EditorInfo.IME_ACTION_SEND);
        composer.setBackgroundColor(Color.TRANSPARENT);
        composer.setPadding(0, 0, dp(8), 0);
        composer.setOnEditorActionListener((view, actionId, event) -> {
            boolean keyboardSend = actionId == EditorInfo.IME_ACTION_SEND;
            boolean hardwareEnter =
                event != null
                    && event.getKeyCode() == KeyEvent.KEYCODE_ENTER
                    && event.getAction() == KeyEvent.ACTION_DOWN;
            if (keyboardSend || hardwareEnter) {
                sendMessage();
                return true;
            }
            return false;
        });
        composer.addTextChangedListener(new TextWatcher() {
            @Override public void beforeTextChanged(CharSequence value, int start, int count, int after) {}
            @Override public void onTextChanged(CharSequence value, int start, int before, int count) {
                updateSendButton();
            }
            @Override public void afterTextChanged(Editable value) {}
        });
        row.addView(composer, new LinearLayout.LayoutParams(
            0,
            dp(44),
            1f
        ));

        micButton = iconButton(R.drawable.ic_mic, "Start voice", WHITE, BLACK);
        micButton.setOnClickListener(view -> toggleVoice());
        row.addView(micButton, iconParams(dp(42), dp(6)));

        sendButton = iconButton(R.drawable.ic_send, "Send message", BLACK, WHITE);
        sendButton.setOnClickListener(view -> sendMessage());
        row.addView(sendButton, iconParams(dp(42), 0));

        wrapper.addView(row, matchWrap());
        updateSendButton();
        return wrapper;
    }

    private void applySystemInsets() {
        root.setOnApplyWindowInsetsListener((view, windowInsets) -> {
            Insets bars = windowInsets.getInsets(WindowInsets.Type.systemBars());
            Insets ime = windowInsets.getInsets(WindowInsets.Type.ime());
            int bottomInset = Math.max(bars.bottom, ime.bottom);

            topBar.setPadding(
                dp(16) + bars.left,
                dp(9) + bars.top,
                dp(12) + bars.right,
                dp(9)
            );
            messageScroll.setPadding(
                dp(14) + bars.left,
                dp(8),
                dp(14) + bars.right,
                dp(14)
            );
            composerShell.setPadding(
                dp(12) + bars.left,
                dp(6),
                dp(12) + bars.right,
                dp(10) + bottomInset
            );
            return windowInsets;
        });
        root.requestApplyInsets();
    }

    private void startJarvisIfConfigured() {
        if (!store.hasMobileToken() || store.coreUrl().isBlank()) return;

        boolean microphoneGranted =
            checkSelfPermission(Manifest.permission.RECORD_AUDIO)
                == PackageManager.PERMISSION_GRANTED;
        boolean assistantHostsWake =
            store.assistantWakeAlways()
                && JarvisVoiceInteractionService.isActiveAssistant(this);
        String action =
            microphoneGranted
                && store.wakeEnabled()
                && !assistantHostsWake
                    ? VoiceService.ACTION_ARM_WAKE
                    : VoiceService.ACTION_START;

        startForegroundService(
            new Intent(this, VoiceService.class).setAction(action)
        );
        if (microphoneGranted) {
            JarvisVoiceInteractionService.refreshWakeIfActive(this);
        }
    }

    private void sendMessage() {
        String value = composer.getText().toString().trim();
        if (value.isEmpty()) return;
        if (!credentialsReady()) return;
        composer.setText("");
        startForegroundService(
            new Intent(this, VoiceService.class)
                .setAction(VoiceService.ACTION_SEND_TEXT)
                .putExtra(VoiceService.EXTRA_TEXT, value)
        );
    }

    private void toggleVoice() {
        if (voiceActive) {
            startService(
                new Intent(this, VoiceService.class)
                    .setAction(VoiceService.ACTION_STOP_VOICE)
            );
            return;
        }

        if (!credentialsReady()) return;
        List<String> missing = new ArrayList<>();
        if (checkSelfPermission(Manifest.permission.RECORD_AUDIO)
                != PackageManager.PERMISSION_GRANTED) {
            missing.add(Manifest.permission.RECORD_AUDIO);
        }
        if (android.os.Build.VERSION.SDK_INT >= 33
                && checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS)
                    != PackageManager.PERMISSION_GRANTED) {
            missing.add(Manifest.permission.POST_NOTIFICATIONS);
        }
        if (!missing.isEmpty()) {
            requestPermissions(
                missing.toArray(new String[0]),
                REQUEST_VOICE_PERMISSIONS
            );
            return;
        }

        startForegroundService(
            new Intent(this, VoiceService.class)
                .setAction(VoiceService.ACTION_START_VOICE)
        );
    }

    @Override public void onRequestPermissionsResult(
        int requestCode,
        String[] permissions,
        int[] grantResults
    ) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults);
        if (requestCode != REQUEST_VOICE_PERMISSIONS) return;
        for (int result : grantResults) {
            if (result != PackageManager.PERMISSION_GRANTED) {
                Toast.makeText(
                    this,
                    "Microphone permission is required for voice",
                    Toast.LENGTH_LONG
                ).show();
                return;
            }
        }
        toggleVoice();
    }

    private boolean credentialsReady() {
        if (store.coreUrl().isBlank() || store.mobileToken().isBlank()) {
            Toast.makeText(
                this,
                "Open Settings and add the Jarvis Core URL and mobile token",
                Toast.LENGTH_LONG
            ).show();
            startActivity(new Intent(this, SettingsActivity.class));
            return false;
        }
        if (VoiceCatalog.isOriginal(store.voiceId())
                && (store.homeAssistantUrl().isBlank()
                    || store.homeAssistantToken().isBlank())) {
            Toast.makeText(
                this,
                "The original Jarvis voice also needs Home Assistant details",
                Toast.LENGTH_LONG
            ).show();
            startActivity(new Intent(this, SettingsActivity.class));
            return false;
        }
        return true;
    }

    private void newChat() {
        history.clear();
        store.newConversationId();
        renderHistory();
        startForegroundService(
            new Intent(this, VoiceService.class)
                .setAction(VoiceService.ACTION_NEW_CHAT)
        );
    }

    private void renderHistory() {
        if (messageList == null) return;
        messageList.removeAllViews();
        streamingText = null;
        List<ChatMessage> messages = history.list();
        emptyState.setVisibility(messages.isEmpty() ? View.VISIBLE : View.GONE);
        for (ChatMessage message : messages) {
            addMessageView(message, false);
        }
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
        content.setLineSpacing(0f, 1.14f);
        content.setPadding(
            user ? dp(14) : 0,
            dp(10),
            user ? dp(14) : dp(8),
            dp(10)
        );
        if (user) {
            content.setBackground(rounded(SOFT, 18, 0, Color.TRANSPARENT));
        }

        LinearLayout.LayoutParams contentParams = new LinearLayout.LayoutParams(
            ViewGroup.LayoutParams.WRAP_CONTENT,
            ViewGroup.LayoutParams.WRAP_CONTENT
        );
        if (user) {
            contentParams.width = Math.min(
                getResources().getDisplayMetrics().widthPixels - dp(80),
                dp(520)
            );
        }
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

        streamingText = text("Thinking...", 16, MID);
        streamingText.setLineSpacing(0f, 1.14f);
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
        if ("Thinking...".equals(current)) current = "";
        streamingText.setText(current + delta);
        streamingText.setTextColor(BLACK);
        scrollToBottom();
    }

    private void finishStreaming() {
        if (streamingText == null) return;
        View parent = (View) streamingText.getParent();
        if (parent != null && parent.getParent() == messageList) {
            messageList.removeView(parent);
        }
        streamingText = null;
    }

    private void scrollToBottom() {
        messageScroll.post(() -> messageScroll.fullScroll(View.FOCUS_DOWN));
    }

    private void updateMicButton() {
        if (micButton == null) return;
        if (voiceActive) {
            micButton.setBackground(rounded(BLACK, 21, 0, Color.TRANSPARENT));
            micButton.setColorFilter(WHITE);
            micButton.setContentDescription(
                listening ? "Listening. Tap to stop voice." : "Stop voice"
            );
            micButton.setAlpha(1f);
        } else {
            micButton.setBackground(rounded(WHITE, 21, 1, LINE));
            micButton.setColorFilter(BLACK);
            micButton.setContentDescription("Start voice");
            micButton.setAlpha(1f);
        }
    }

    private void updateSendButton() {
        if (sendButton == null || composer == null) return;
        boolean enabled = !composer.getText().toString().trim().isEmpty();
        sendButton.setEnabled(enabled);
        sendButton.setAlpha(enabled ? 1f : 0.35f);
    }

    private ImageButton iconButton(
        int icon,
        String description,
        int background,
        int foreground
    ) {
        ImageButton value = new ImageButton(this);
        value.setImageResource(icon);
        value.setContentDescription(description);
        value.setColorFilter(foreground);
        value.setScaleType(ImageButton.ScaleType.CENTER);
        value.setPadding(dp(10), dp(10), dp(10), dp(10));
        value.setMinimumWidth(0);
        value.setMinimumHeight(0);
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

    private GradientDrawable rounded(
        int fill,
        int radiusDp,
        int strokeDp,
        int strokeColour
    ) {
        GradientDrawable background = new GradientDrawable();
        background.setColor(fill);
        background.setCornerRadius(dp(radiusDp));
        if (strokeDp > 0) {
            background.setStroke(dp(strokeDp), strokeColour);
        }
        return background;
    }

    private LinearLayout.LayoutParams iconParams(int size, int rightMargin) {
        LinearLayout.LayoutParams params =
            new LinearLayout.LayoutParams(size, size);
        params.rightMargin = rightMargin;
        return params;
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
