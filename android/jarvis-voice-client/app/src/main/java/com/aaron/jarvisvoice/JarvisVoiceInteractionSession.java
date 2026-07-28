package com.aaron.jarvisvoice;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.content.IntentFilter;
import android.graphics.Color;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.os.Bundle;
import android.service.voice.VoiceInteractionSession;
import android.text.InputType;
import android.view.Gravity;
import android.view.KeyEvent;
import android.view.View;
import android.view.ViewGroup;
import android.view.WindowInsets;
import android.view.inputmethod.EditorInfo;
import android.widget.Button;
import android.widget.EditText;
import android.widget.FrameLayout;
import android.widget.ImageButton;
import android.widget.LinearLayout;
import android.widget.TextView;

/** Compact system-assistant overlay shown above the current app. */
public final class JarvisVoiceInteractionSession extends VoiceInteractionSession {
    private static final int BLACK = Color.rgb(20, 20, 20);
    private static final int MID = Color.rgb(103, 103, 103);
    private static final int LINE = Color.rgb(226, 226, 226);
    private static final int SOFT = Color.rgb(246, 246, 246);
    private static final int WHITE = Color.WHITE;

    private final Context context;
    private TextView status;
    private TextView transcript;
    private EditText composer;
    private ImageButton micButton;
    private ImageButton sendButton;
    private boolean voiceActive;
    private boolean listening;
    private final StringBuilder streamed = new StringBuilder();

    private final BroadcastReceiver receiver = new BroadcastReceiver() {
        @Override public void onReceive(Context ignored, Intent intent) {
            if (!VoiceService.ACTION_EVENT.equals(intent.getAction())) return;
            String event = safe(intent.getStringExtra(VoiceService.EXTRA_EVENT));
            String role = safe(intent.getStringExtra(VoiceService.EXTRA_ROLE));
            String text = safe(intent.getStringExtra(VoiceService.EXTRA_TEXT));
            voiceActive = intent.getBooleanExtra(
                VoiceService.EXTRA_ACTIVE,
                voiceActive
            );
            listening = intent.getBooleanExtra(
                VoiceService.EXTRA_LISTENING,
                listening
            );
            updateMicButton();

            switch (event) {
                case "status" -> status.setText(text);
                case "thinking" -> {
                    streamed.setLength(0);
                    transcript.setText("Thinking...");
                    transcript.setTextColor(MID);
                }
                case "assistant_delta" -> {
                    streamed.append(text);
                    transcript.setText(streamed.toString());
                    transcript.setTextColor(BLACK);
                }
                case "message" -> {
                    if (ChatMessage.ASSISTANT.equals(role)) {
                        streamed.setLength(0);
                        transcript.setText(text);
                        transcript.setTextColor(BLACK);
                    } else if (ChatMessage.USER.equals(role)) {
                        status.setText("You: " + text);
                    }
                }
                case "draft" ->
                    status.setText(text.isBlank() ? "Listening" : text);
                case "error" -> {
                    status.setText("Something went wrong");
                    transcript.setText(text);
                    transcript.setTextColor(BLACK);
                }
                default -> { }
            }
        }
    };

    public JarvisVoiceInteractionSession(Context context) {
        super(context);
        this.context = context;
        setTheme(R.style.Theme_JarvisAssistantOverlay);
    }

    @Override public void onCreate() {
        super.onCreate();
        IntentFilter filter = new IntentFilter(VoiceService.ACTION_EVENT);
        if (android.os.Build.VERSION.SDK_INT >= 33) {
            context.registerReceiver(
                receiver,
                filter,
                Context.RECEIVER_NOT_EXPORTED
            );
        } else {
            context.registerReceiver(receiver, filter);
        }
    }

    @Override public View onCreateContentView() {
        FrameLayout root = new FrameLayout(context);
        root.setBackgroundColor(Color.TRANSPARENT);
        root.setOnClickListener(view -> finish());

        LinearLayout card = new LinearLayout(context);
        card.setOrientation(LinearLayout.VERTICAL);
        card.setPadding(dp(18), dp(14), dp(18), dp(14));
        card.setBackground(rounded(WHITE, 24, 1, LINE));
        card.setElevation(dp(14));
        card.setOnClickListener(view -> { });

        LinearLayout header = new LinearLayout(context);
        header.setOrientation(LinearLayout.HORIZONTAL);
        header.setGravity(Gravity.CENTER_VERTICAL);

        TextView title = text("Jarvis", 19, BLACK);
        title.setTypeface(Typeface.create("sans-serif-medium", Typeface.NORMAL));
        header.addView(
            title,
            new LinearLayout.LayoutParams(
                0,
                ViewGroup.LayoutParams.WRAP_CONTENT,
                1f
            )
        );

        Button open = textButton("Open chat");
        open.setOnClickListener(view -> openFullChat());
        header.addView(open, wrapWrap(0, dp(4)));

        Button close = textButton("Close");
        close.setOnClickListener(view -> finish());
        header.addView(close, wrapWrap());
        card.addView(header, matchWrap());

        status = text("Starting...", 12, MID);
        status.setPadding(0, dp(5), 0, dp(8));
        status.setMaxLines(1);
        card.addView(status, matchWrap());

        transcript = text("How can I help?", 18, BLACK);
        transcript.setLineSpacing(0f, 1.14f);
        transcript.setMinHeight(dp(70));
        transcript.setMaxLines(6);
        transcript.setPadding(0, dp(8), 0, dp(12));
        card.addView(transcript, matchWrap());

        LinearLayout composerRow = new LinearLayout(context);
        composerRow.setOrientation(LinearLayout.HORIZONTAL);
        composerRow.setGravity(Gravity.CENTER_VERTICAL);
        composerRow.setPadding(dp(11), dp(6), dp(6), dp(6));
        composerRow.setBackground(rounded(SOFT, 23, 1, LINE));

        composer = new EditText(context);
        composer.setHint("Message Jarvis");
        composer.setHintTextColor(Color.rgb(132, 132, 132));
        composer.setTextColor(BLACK);
        composer.setTextSize(15);
        composer.setSingleLine(true);
        composer.setInputType(
            InputType.TYPE_CLASS_TEXT
                | InputType.TYPE_TEXT_FLAG_CAP_SENTENCES
                | InputType.TYPE_TEXT_FLAG_AUTO_CORRECT
        );
        composer.setBackgroundColor(Color.TRANSPARENT);
        composer.setImeOptions(EditorInfo.IME_ACTION_SEND);
        composer.setOnEditorActionListener((view, actionId, event) -> {
            boolean keyboardSend = actionId == EditorInfo.IME_ACTION_SEND;
            boolean hardwareEnter =
                event != null
                    && event.getKeyCode() == KeyEvent.KEYCODE_ENTER
                    && event.getAction() == KeyEvent.ACTION_DOWN;
            if (keyboardSend || hardwareEnter) {
                sendText();
                return true;
            }
            return false;
        });
        composerRow.addView(
            composer,
            new LinearLayout.LayoutParams(0, dp(42), 1f)
        );

        micButton = iconButton(
            R.drawable.ic_mic,
            "Start voice",
            WHITE,
            BLACK
        );
        micButton.setOnClickListener(view -> toggleVoice());
        composerRow.addView(micButton, iconParams(dp(40), dp(6)));

        sendButton = iconButton(
            R.drawable.ic_send,
            "Send message",
            BLACK,
            WHITE
        );
        sendButton.setOnClickListener(view -> sendText());
        composerRow.addView(sendButton, iconParams(dp(40), 0));

        card.addView(composerRow, matchWrap());

        FrameLayout.LayoutParams cardParams = new FrameLayout.LayoutParams(
            ViewGroup.LayoutParams.MATCH_PARENT,
            ViewGroup.LayoutParams.WRAP_CONTENT,
            Gravity.BOTTOM
        );
        root.addView(card, cardParams);

        root.setOnApplyWindowInsetsListener((view, insets) -> {
            android.graphics.Insets bars =
                insets.getInsets(WindowInsets.Type.systemBars());
            android.graphics.Insets ime =
                insets.getInsets(WindowInsets.Type.ime());
            int bottom = Math.max(bars.bottom, ime.bottom);
            root.setPadding(
                dp(12) + bars.left,
                dp(12),
                dp(12) + bars.right,
                dp(14) + bottom
            );
            return insets;
        });
        root.requestApplyInsets();
        updateMicButton();
        return root;
    }

    @Override public void onShow(Bundle args, int showFlags) {
        super.onShow(args, showFlags);
        setKeepAwake(true);
        String command = args == null
            ? ""
            : safe(args.getString(JarvisVoiceInteractionService.ARG_COMMAND));
        Intent invoke = new Intent(context, VoiceService.class)
            .setAction(VoiceService.ACTION_ASSISTANT_INVOKE)
            .putExtra(VoiceService.EXTRA_TEXT, command);
        context.startForegroundService(invoke);
    }

    @Override public void onHide() {
        setKeepAwake(false);
        try {
            context.startService(
                new Intent(context, VoiceService.class)
                    .setAction(VoiceService.ACTION_ASSISTANT_DISMISS)
            );
        } catch (Exception ignored) {}
        JarvisVoiceInteractionService.rearmWakeIfActive(context);
        super.onHide();
    }

    @Override public void onDestroy() {
        try { context.unregisterReceiver(receiver); } catch (Exception ignored) {}
        super.onDestroy();
    }

    private void sendText() {
        String value = composer.getText().toString().trim();
        if (value.isEmpty()) return;
        composer.setText("");
        context.startForegroundService(
            new Intent(context, VoiceService.class)
                .setAction(VoiceService.ACTION_SEND_TEXT)
                .putExtra(VoiceService.EXTRA_TEXT, value)
        );
    }

    private void toggleVoice() {
        String action = voiceActive
            ? VoiceService.ACTION_STOP_VOICE
            : VoiceService.ACTION_START_VOICE;
        context.startForegroundService(
            new Intent(context, VoiceService.class).setAction(action)
        );
    }

    private void openFullChat() {
        Intent open = new Intent(context, MainActivity.class)
            .addFlags(
                Intent.FLAG_ACTIVITY_NEW_TASK
                    | Intent.FLAG_ACTIVITY_CLEAR_TOP
            );
        context.startActivity(open);
        finish();
    }

    private void updateMicButton() {
        if (micButton == null) return;
        if (voiceActive) {
            micButton.setBackground(rounded(BLACK, 20, 0, Color.TRANSPARENT));
            micButton.setColorFilter(WHITE);
            micButton.setContentDescription(
                listening ? "Listening. Tap to stop." : "Stop voice"
            );
        } else {
            micButton.setBackground(rounded(WHITE, 20, 1, LINE));
            micButton.setColorFilter(BLACK);
            micButton.setContentDescription("Start voice");
        }
    }

    private Button textButton(String label) {
        Button value = new Button(context);
        value.setText(label);
        value.setTextSize(12);
        value.setTextColor(BLACK);
        value.setAllCaps(false);
        value.setMinWidth(0);
        value.setMinimumWidth(0);
        value.setMinHeight(0);
        value.setMinimumHeight(0);
        value.setPadding(dp(8), dp(5), dp(8), dp(5));
        value.setBackgroundColor(Color.TRANSPARENT);
        return value;
    }

    private ImageButton iconButton(
        int icon,
        String description,
        int background,
        int foreground
    ) {
        ImageButton value = new ImageButton(context);
        value.setImageResource(icon);
        value.setContentDescription(description);
        value.setColorFilter(foreground);
        value.setScaleType(ImageButton.ScaleType.CENTER);
        value.setPadding(dp(9), dp(9), dp(9), dp(9));
        value.setMinimumWidth(0);
        value.setMinimumHeight(0);
        value.setBackground(rounded(background, 20, 0, Color.TRANSPARENT));
        return value;
    }

    private TextView text(String value, int size, int colour) {
        TextView view = new TextView(context);
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
        if (strokeDp > 0) background.setStroke(dp(strokeDp), strokeColour);
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
        return Math.round(
            value * context.getResources().getDisplayMetrics().density
        );
    }

    private static String safe(String value) {
        return value == null ? "" : value;
    }
}
