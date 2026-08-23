package com.aaron.jarvisvoice;

import android.Manifest;
import android.app.Activity;
import android.app.Dialog;
import android.content.BroadcastReceiver;
import android.content.ClipData;
import android.content.ClipboardManager;
import android.content.Context;
import android.content.Intent;
import android.content.IntentFilter;
import android.content.pm.PackageManager;
import android.database.Cursor;
import android.graphics.Color;
import android.graphics.Insets;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.os.Bundle;
import android.provider.OpenableColumns;
import android.speech.RecognizerIntent;
import android.text.Editable;
import android.text.InputType;
import android.text.TextWatcher;
import android.text.method.LinkMovementMethod;
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
import android.widget.Button;
import android.widget.FrameLayout;
import android.widget.ImageButton;
import android.widget.LinearLayout;
import android.widget.PopupMenu;
import android.widget.ScrollView;
import android.widget.TextView;
import android.widget.Toast;

import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import android.util.Base64;
import org.json.JSONArray;
import org.json.JSONObject;

public final class MainActivity extends Activity {
    private static final int BLACK = Color.rgb(20, 20, 20);
    private static final int MID = Color.rgb(103, 103, 103);
    private static final int LINE = Color.rgb(226, 226, 226);
    private static final int SOFT = Color.rgb(246, 246, 246);
    private static final int WHITE = Color.WHITE;
    private static final int REQUEST_VOICE_PERMISSIONS = 1800;
    private static final int REQUEST_DEVELOPER_DICTATION = 1801;
    private static final int REQUEST_DEVELOPER_ATTACHMENT = 1802;
    private static final int MAX_DEVELOPER_ATTACHMENT_BYTES = 1_500_000;

    private SecureStore store;
    private ChatHistoryStore history;
    private LinearLayout root;
    private LinearLayout topBar;
    private LinearLayout composerShell;
    private ScrollView messageScroll;
    private LinearLayout messageList;
    private EditText composer;
    private TextView statusText;
    private TextView modeText;
    private ImageButton micButton;
    private ImageButton sendButton;
    private View emptyState;
    private TextView streamingText;
    private boolean voiceActive;
    private boolean listening;
    private boolean generating;
    private AssistantMode assistantMode;
    private DeveloperClient developerClient;
    private final Map<String, TextView> developerActivityStatuses = new HashMap<>();
    private JSONArray pendingDeveloperAttachments = new JSONArray();

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
                    if (ChatMessage.ASSISTANT.equals(role)) {
                        generating = false;
                    }
                    addMessageView(
                        new ChatMessage(
                            role,
                            text,
                            System.currentTimeMillis()
                        ),
                        true
                    );
                    updateSendButton();
                }
                case "assistant_delta" -> {
                    generating = true;
                    appendStreaming(text);
                    updateSendButton();
                }
                case "thinking" -> {
                    generating = true;
                    beginStreaming();
                    updateSendButton();
                }
                case "draft" -> statusText.setText(
                    text.isBlank() ? "Listening" : text
                );
                case "clear", "chat.switched" -> {
                    generating = false;
                    finishStreaming();
                    renderHistory();
                    updateSendButton();
                }
                case "generation.cancelled" -> {
                    generating = false;
                    finishStreaming();
                    statusText.setText("Stopped");
                    updateSendButton();
                }
                case "conversation.ended" ->
                    statusText.setText(
                        "Dedicated wake word ready"
                    );
                case "error" -> {
                    generating = false;
                    finishStreaming();
                    updateSendButton();
                    statusText.setText("Something went wrong");
                    Toast.makeText(
                        MainActivity.this,
                        text,
                        Toast.LENGTH_LONG
                    ).show();
                }
                default -> { }
            }
        }
    };

    public static boolean isVisible() {
        return AppVisibility.isVisible();
    }

    @Override protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        store = new SecureStore(this);
        assistantMode = store.assistantMode();
        developerClient = new DeveloperClient(this, new DeveloperClient.Listener() {
            @Override public void onState(String state) { statusText.setText(state); }
            @Override public void onEvent(JSONObject event) { renderDeveloperEvent(event); }
            @Override public void onError(String message) { Toast.makeText(MainActivity.this, message, Toast.LENGTH_SHORT).show(); }
        });
        new UpdatePreferences(this).recordLaunch(JarvisVersion.RELEASE);
        UpdateManager.schedule(this);
        history = new ChatHistoryStore(this);
        configureWindow();
        setContentView(buildContent());
        applySystemBarAppearance();
        applySystemInsets();
        if (!DeveloperRoutingPolicy.routesToDeveloper(assistantMode)) renderHistory();
        updateMicButton();
        startJarvisIfConfigured();
        applyAssistantMode(false);
    }

    @Override protected void onStart() {
        super.onStart();
        AppVisibility.activityStarted();
        IntentFilter filter = new IntentFilter(VoiceService.ACTION_EVENT);
        androidx.core.content.ContextCompat.registerReceiver(
            this, receiver, filter, androidx.core.content.ContextCompat.RECEIVER_NOT_EXPORTED
        );
        if (!DeveloperRoutingPolicy.routesToDeveloper(assistantMode)) renderHistory();
    }

    @Override protected void onResume() {
        super.onResume();
        updateMicButton();
        JarvisVoiceInteractionService.ensureWakeIfActive(this);
    }

    @Override protected void onStop() {
        AppVisibility.activityStopped();
        try { unregisterReceiver(receiver); } catch (Exception ignored) {}
        super.onStop();
    }

    @Override protected void onDestroy() {
        if (developerClient != null) developerClient.destroy();
        super.onDestroy();
    }

    private void configureWindow() {
        Window window = getWindow();
        window.setStatusBarColor(Color.TRANSPARENT);
        window.setNavigationBarColor(Color.TRANSPARENT);
        window.setNavigationBarDividerColor(Color.TRANSPARENT);
        window.setSoftInputMode(
            WindowManager.LayoutParams.SOFT_INPUT_ADJUST_RESIZE
        );
    }

    private void applySystemBarAppearance() {
        View decorView = getWindow().getDecorView();

        decorView.post(() -> {
            WindowInsetsController controller =
                decorView.getWindowInsetsController();

            if (controller == null) return;

            int appearance =
                WindowInsetsController.APPEARANCE_LIGHT_STATUS_BARS
                    | WindowInsetsController.APPEARANCE_LIGHT_NAVIGATION_BARS;

            controller.setSystemBarsAppearance(
                appearance,
                appearance
            );
        });
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
        TextView title = text("J A R V I S", 18, BLACK);
        title.setTypeface(Typeface.create("sans-serif-medium", Typeface.NORMAL));
        title.setLetterSpacing(0.12f);
        titleBlock.addView(title, matchWrap());
        modeText = text("Jarvis  ⌄", 13, BLACK);
        modeText.setTypeface(Typeface.create("sans-serif-medium", Typeface.NORMAL));
        modeText.setPadding(0, dp(3), dp(12), dp(2));
        modeText.setOnClickListener(view -> showModePicker());
        titleBlock.addView(modeText, matchWrap());
        statusText = text("Connecting", 12, MID);
        statusText.setMaxLines(1);
        statusText.setPadding(0, dp(2), dp(8), 0);
        titleBlock.addView(statusText, matchWrap());
        bar.addView(titleBlock, new LinearLayout.LayoutParams(
            0,
            ViewGroup.LayoutParams.WRAP_CONTENT,
            1f
        ));

        ImageButton proactiveButton = iconButton(
            R.drawable.ic_notifications,
            "House activity",
            SOFT,
            BLACK
        );
        proactiveButton.setOnClickListener(view ->
            startActivity(
                new Intent(this, ProactiveActivity.class)
            )
        );
        bar.addView(
            proactiveButton,
            iconParams(dp(40), dp(6))
        );

        ImageButton newChat = iconButton(
            R.drawable.ic_add,
            "New chat",
            SOFT,
            BLACK
        );
        newChat.setOnClickListener(view -> newChat());
        bar.addView(
            newChat,
            iconParams(dp(40), dp(6))
        );

        ImageButton clearChat = assetButton(
            R.drawable.control_delete_red,
            "Clear current chat"
        );
        clearChat.setOnClickListener(view -> confirmDeleteChat());
        bar.addView(clearChat, iconParams(dp(44), dp(6)));

        ImageButton settings = iconButton(
            R.drawable.ic_settings,
            "Settings",
            SOFT,
            BLACK
        );
        settings.setOnClickListener(view ->
            startActivity(new Intent(this, SettingsActivity.class))
        );
        bar.addView(
            settings,
            iconParams(dp(40), 0)
        );
        return bar;
    }

    private void showTopMenu(View anchor) {
        PopupMenu menu = new PopupMenu(this, anchor);
        menu.getMenu().add(0, 1, 0, "Chat history");
        menu.getMenu().add(0, 4, 1, "Improvements");
        menu.getMenu().add(0, 2, 2, "Delete current chat");
        menu.getMenu().add(0, 3, 3, "Settings");
        menu.setOnMenuItemClickListener(item -> {
            return switch (item.getItemId()) {
                case 1 -> {
                    openHistory();
                    yield true;
                }
                case 4 -> {
                    startActivity(
                        new Intent(this, ImprovementsActivity.class)
                    );
                    yield true;
                }
                case 2 -> {
                    confirmDeleteChat();
                    yield true;
                }
                case 3 -> {
                    startActivity(
                        new Intent(this, SettingsActivity.class)
                    );
                    yield true;
                }
                default -> false;
            };
        });
        menu.show();
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

        ImageButton addButton = assetButton(R.drawable.control_chat, "Chat options");
        addButton.setOnClickListener(this::showComposerActions);
        row.addView(addButton, iconParams(dp(42), dp(8)));

        composer = new EditText(this);
        composer.setHint("Message Jarvis");
        composer.setHintTextColor(Color.rgb(132, 132, 132));
        composer.setTextColor(BLACK);
        composer.setTextSize(16);
        composer.setSingleLine(false);
        composer.setMinLines(1);
        composer.setMaxLines(6);
        composer.setGravity(Gravity.CENTER_VERTICAL);
        composer.setInputType(
            InputType.TYPE_CLASS_TEXT
                | InputType.TYPE_TEXT_FLAG_CAP_SENTENCES
                | InputType.TYPE_TEXT_FLAG_AUTO_CORRECT
                | InputType.TYPE_TEXT_FLAG_MULTI_LINE
        );
        composer.setImeOptions(
            EditorInfo.IME_FLAG_NO_EXTRACT_UI
        );
        composer.setBackgroundColor(Color.TRANSPARENT);
        composer.setPadding(0, 0, dp(8), 0);
        composer.setOnEditorActionListener(
            (view, actionId, event) -> {
                boolean controlEnter =
                    event != null
                        && event.isCtrlPressed()
                        && event.getKeyCode()
                            == KeyEvent.KEYCODE_ENTER
                        && event.getAction()
                            == KeyEvent.ACTION_DOWN;
                if (controlEnter) {
                    sendMessage();
                    return true;
                }
                return false;
            }
        );
        composer.addTextChangedListener(new TextWatcher() {
            @Override public void beforeTextChanged(CharSequence value, int start, int count, int after) {}
            @Override public void onTextChanged(CharSequence value, int start, int before, int count) {
                updateSendButton();
            }
            @Override public void afterTextChanged(Editable value) {}
        });
        composer.setMinHeight(dp(44));
        row.addView(composer, new LinearLayout.LayoutParams(
            0,
            ViewGroup.LayoutParams.WRAP_CONTENT,
            1f
        ));

        micButton = assetButton(R.drawable.control_mic, "Start voice");
        micButton.setOnClickListener(view -> {
            if (DeveloperRoutingPolicy.routesToDeveloper(assistantMode)) startDeveloperDictation();
            else toggleVoice();
        });
        row.addView(micButton, iconParams(dp(42), dp(6)));

        sendButton = iconButton(
            R.drawable.ic_send,
            "Send message",
            BLACK,
            WHITE
        );
        sendButton.setOnClickListener(
            view -> handleSendOrStop()
        );
        row.addView(sendButton, iconParams(dp(42), 0));

        wrapper.addView(row, matchWrap());
        updateSendButton();
        return wrapper;
    }

    private void showComposerActions(View anchor) {
        PopupMenu menu = new PopupMenu(this, anchor);
        if (DeveloperRoutingPolicy.routesToDeveloper(assistantMode)) {
            menu.getMenu().add("Attach image or log").setOnMenuItemClickListener(item -> {
                Intent picker = new Intent(Intent.ACTION_OPEN_DOCUMENT)
                    .addCategory(Intent.CATEGORY_OPENABLE).setType("*/*")
                    .putExtra(Intent.EXTRA_MIME_TYPES, new String[]{"image/png", "image/jpeg", "image/webp", "text/plain", "text/markdown", "application/json"});
                startActivityForResult(picker, REQUEST_DEVELOPER_ATTACHMENT);
                return true;
            });
            menu.getMenu().add("New development session").setOnMenuItemClickListener(item -> {
                developerClient.newSession();
                store.setDeveloperThreadId("");
                finishStreaming(); messageList.removeAllViews(); developerActivityStatuses.clear();
                emptyState.setVisibility(View.VISIBLE);
                statusText.setText("Connected");
                return true;
            });
            menu.getMenu().add("Recent development sessions").setOnMenuItemClickListener(item -> {
                developerClient.listThreads();
                statusText.setText("Loading sessions…");
                return true;
            });
            menu.getMenu().add("Use Jarvis Wear workspace").setOnMenuItemClickListener(item -> {
                switchDeveloperWorkspace("jarvis-wear"); return true;
            });
            menu.getMenu().add("Use Jarvis workspace").setOnMenuItemClickListener(item -> {
                switchDeveloperWorkspace("jarvis"); return true;
            });
            menu.show();
            return;
        }
        menu.getMenu().add("New chat").setOnMenuItemClickListener(item -> {
            newChat();
            return true;
        });
        menu.getMenu().add("Chat history").setOnMenuItemClickListener(item -> {
            openHistory();
            return true;
        });
        menu.getMenu().add("Improvements").setOnMenuItemClickListener(item -> {
            startActivity(new Intent(this, ImprovementsActivity.class));
            return true;
        });
        menu.show();
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
            JarvisVoiceInteractionService.ensureWakeIfActive(this);
        }
    }

    private void sendMessage() {
        String value = composer.getText().toString().trim();
        if (value.isEmpty()) return;
        if (DeveloperRoutingPolicy.routesToDeveloper(assistantMode)) {
            composer.setText("");
            addMessageView(new ChatMessage(ChatMessage.USER, value, System.currentTimeMillis()), true);
            JSONArray attachments = pendingDeveloperAttachments;
            pendingDeveloperAttachments = new JSONArray();
            developerClient.sendInstruction(value, attachments);
            composer.post(() -> composer.requestFocus());
            return;
        }
        if (!credentialsReady()) return;
        composer.setText("");
        composer.post(() -> {
            composer.requestFocus();
            composer.setSelection(composer.length());
        });
        startForegroundService(
            new Intent(this, VoiceService.class)
                .setAction(VoiceService.ACTION_SEND_TEXT)
                .putExtra(VoiceService.EXTRA_TEXT, value)
        );
    }

    private void showModePicker() {
        Dialog dialog = new Dialog(this);
        LinearLayout sheet = new LinearLayout(this);
        sheet.setOrientation(LinearLayout.VERTICAL);
        sheet.setPadding(dp(22), dp(18), dp(22), dp(20));
        sheet.setBackground(rounded(WHITE, 28, 1, LINE));
        TextView heading = text("Choose mode", 20, BLACK);
        heading.setTypeface(Typeface.create("sans-serif-medium", Typeface.NORMAL));
        sheet.addView(heading, matchWrap(0, dp(12)));
        sheet.addView(modeChoice(dialog, AssistantMode.JARVIS, "Jarvis", "Home, voice, memory and chat"), matchWrap(0, dp(7)));
        sheet.addView(modeChoice(dialog, AssistantMode.DEVELOPER, "Developer", "Build and improve Jarvis"), matchWrap());
        dialog.setContentView(sheet);
        dialog.show();
        Window window = dialog.getWindow();
        if (window != null) {
            window.setBackgroundDrawableResource(android.R.color.transparent);
            window.setDimAmount(0.20f); window.addFlags(WindowManager.LayoutParams.FLAG_DIM_BEHIND);
            window.setGravity(Gravity.BOTTOM);
            WindowManager.LayoutParams params = window.getAttributes();
            params.width = WindowManager.LayoutParams.MATCH_PARENT;
            params.horizontalMargin = 0.03f; params.verticalMargin = 0.02f;
            window.setAttributes(params);
        }
    }

    private View modeChoice(Dialog dialog, AssistantMode mode, String title, String description) {
        LinearLayout row = new LinearLayout(this);
        row.setOrientation(LinearLayout.HORIZONTAL); row.setGravity(Gravity.CENTER_VERTICAL);
        row.setPadding(dp(15), dp(13), dp(15), dp(13));
        row.setBackground(rounded(mode == assistantMode ? SOFT : WHITE, 20, 1, LINE));
        LinearLayout copy = new LinearLayout(this); copy.setOrientation(LinearLayout.VERTICAL);
        TextView name = text(title, 16, BLACK);
        name.setTypeface(Typeface.create("sans-serif-medium", Typeface.NORMAL));
        copy.addView(name, matchWrap()); copy.addView(text(description, 13, MID), matchWrap());
        row.addView(copy, new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f));
        if (mode == assistantMode) row.addView(text("✓", 18, BLACK), wrapWrap());
        row.setOnClickListener(view -> {
            dialog.dismiss(); assistantMode = mode; store.setAssistantMode(mode); applyAssistantMode(true);
        });
        return row;
    }

    private void applyAssistantMode(boolean clearView) {
        if (modeText == null || composer == null) return;
        boolean developer = DeveloperRoutingPolicy.routesToDeveloper(assistantMode);
        modeText.setText(developer ? "Developer  ⌄" : "Jarvis  ⌄");
        composer.setHint(DeveloperRoutingPolicy.placeholder(assistantMode));
        micButton.setVisibility(View.VISIBLE);
        micButton.setContentDescription(developer ? "Dictate developer instruction" : "Start voice");
        if (clearView || developer) {
            finishStreaming(); messageList.removeAllViews(); developerActivityStatuses.clear();
            emptyState.setVisibility(View.VISIBLE);
        }
        if (developer) {
            statusText.setText("Connecting…");
            if (store.developerToken().isBlank()) {
                statusText.setText("Setup required");
                Toast.makeText(this, "Add the Developer token in Settings", Toast.LENGTH_LONG).show();
            } else {
                developerClient.connect(store.developerUrl(), store.remoteDeveloperUrl(),
                    store.developerToken(), store.developerWorkspace(), store.developerThreadId());
            }
        } else {
            developerClient.close(); renderHistory(); statusText.setText("Ready");
        }
    }

    private void renderDeveloperEvent(JSONObject event) {
        String method = event.optString("method");
        JSONObject params = event.optJSONObject("params");
        if (event.has("id") && method.endsWith("requestApproval")) {
            showDeveloperApproval(event.optLong("id"), method, params);
        } else if ("response".equals(event.optString("type"))
                && "threads.list".equals(event.optString("request_kind"))) {
            showDeveloperThreads(event.optJSONObject("result"));
        } else if ("response".equals(event.optString("type"))
                && "thread.resume".equals(event.optString("request_kind"))) {
            showDeveloperThreadHistory(event.optJSONObject("result"));
        } else if ("item/agentMessage/delta".equals(method)) {
            generating = true;
            appendStreaming(params == null ? "" : params.optString("delta"));
        } else if ("turn/started".equals(method)) {
            generating = true; statusText.setText("Working…"); beginStreaming();
        } else if ("turn/completed".equals(method)) {
            generating = false; finishStreaming(); statusText.setText("Connected");
            store.setDeveloperThreadId(developerClient.threadId());
        } else if ("item/started".equals(method) || "item/completed".equals(method)) {
            JSONObject item = params == null ? null : params.optJSONObject("item");
            if (item != null) {
                String type = item.optString("type", "Activity");
                if ("agentMessage".equals(type) && "item/completed".equals(method)) {
                    finishStreaming();
                    addMessageView(new ChatMessage(ChatMessage.ASSISTANT,
                        item.optString("text"), System.currentTimeMillis()), true);
                } else if (!"agentMessage".equals(type) && !"reasoning".equals(type)) {
                    addDeveloperActivity(item, "item/completed".equals(method));
                }
            }
        }
        updateSendButton();
    }

    private void showDeveloperThreadHistory(JSONObject result) {
        JSONObject thread = result == null ? null : result.optJSONObject("thread");
        JSONArray turns = thread == null ? null : thread.optJSONArray("turns");
        if (turns == null) return;
        finishStreaming(); messageList.removeAllViews(); developerActivityStatuses.clear();
        for (int turnIndex = 0; turnIndex < turns.length(); turnIndex++) {
            JSONObject turn = turns.optJSONObject(turnIndex);
            JSONArray items = turn == null ? null : turn.optJSONArray("items");
            if (items == null) continue;
            for (int itemIndex = 0; itemIndex < items.length(); itemIndex++) {
                JSONObject item = items.optJSONObject(itemIndex);
                if (item == null) continue;
                String type = item.optString("type");
                String text = DeveloperActivityPolicy.messageText(item);
                if (text.isBlank()) continue;
                if ("userMessage".equals(type)) {
                    addMessageView(new ChatMessage(ChatMessage.USER, text, System.currentTimeMillis()), false);
                } else if ("agentMessage".equals(type)) {
                    addMessageView(new ChatMessage(ChatMessage.ASSISTANT, text, System.currentTimeMillis()), false);
                }
            }
        }
        emptyState.setVisibility(messageList.getChildCount() == 0 ? View.VISIBLE : View.GONE);
        scrollToBottom();
    }

    private void switchDeveloperWorkspace(String workspace) {
        store.setDeveloperWorkspace(workspace);
        store.setDeveloperThreadId("");
        developerClient.connect(store.developerUrl(), store.remoteDeveloperUrl(),
            store.developerToken(), workspace, "");
        finishStreaming(); messageList.removeAllViews(); developerActivityStatuses.clear();
        emptyState.setVisibility(View.VISIBLE);
    }

    private void showDeveloperThreads(JSONObject result) {
        JSONArray threads = result == null ? null : result.optJSONArray("data");
        if (threads == null || threads.length() == 0) {
            statusText.setText("Connected");
            Toast.makeText(this, "No recent sessions in this workspace", Toast.LENGTH_SHORT).show();
            return;
        }
        Dialog dialog = new Dialog(this);
        LinearLayout sheet = new LinearLayout(this);
        sheet.setOrientation(LinearLayout.VERTICAL);
        sheet.setPadding(dp(22), dp(18), dp(22), dp(20));
        sheet.setBackground(rounded(WHITE, 28, 1, LINE));
        TextView heading = text("Development sessions", 20, BLACK);
        heading.setTypeface(Typeface.create("sans-serif-medium", Typeface.NORMAL));
        sheet.addView(heading, matchWrap(0, dp(12)));
        int count = Math.min(threads.length(), 8);
        for (int index = 0; index < count; index++) {
            JSONObject thread = threads.optJSONObject(index);
            if (thread == null) continue;
            String id = thread.optString("id");
            String title = thread.optString("name");
            if (title.isBlank()) title = thread.optString("preview", "Development session");
            if (title.length() > 64) title = title.substring(0, 64) + "…";
            TextView row = text(title, 15, BLACK);
            row.setPadding(dp(14), dp(13), dp(14), dp(13));
            row.setBackground(rounded(id.equals(developerClient.threadId()) ? SOFT : WHITE, 18, 1, LINE));
            row.setOnClickListener(view -> {
                dialog.dismiss(); developerClient.selectThread(id); store.setDeveloperThreadId(id);
                finishStreaming(); messageList.removeAllViews(); emptyState.setVisibility(View.VISIBLE);
            });
            sheet.addView(row, matchWrap(0, dp(7)));
        }
        dialog.setContentView(sheet); dialog.show();
        Window window = dialog.getWindow();
        if (window != null) {
            window.setBackgroundDrawableResource(android.R.color.transparent);
            window.setGravity(Gravity.BOTTOM);
            WindowManager.LayoutParams attributes = window.getAttributes();
            attributes.width = WindowManager.LayoutParams.MATCH_PARENT;
            attributes.horizontalMargin = 0.03f; attributes.verticalMargin = 0.02f;
            window.setAttributes(attributes);
        }
    }

    private void showDeveloperApproval(long requestId, String method, JSONObject params) {
        String operation = method.contains("fileChange") ? "modify files" : "run a command";
        String detail = params == null ? "" : params.optString("reason");
        Dialog dialog = new Dialog(this);
        LinearLayout sheet = new LinearLayout(this);
        sheet.setOrientation(LinearLayout.VERTICAL);
        sheet.setPadding(dp(22), dp(18), dp(22), dp(20));
        sheet.setBackground(rounded(WHITE, 28, 1, LINE));
        TextView title = text("Approval required", 20, BLACK);
        title.setTypeface(Typeface.create("sans-serif-medium", Typeface.NORMAL));
        sheet.addView(title, matchWrap(0, dp(8)));
        sheet.addView(text("Developer wants to " + operation + ".", 15, BLACK), matchWrap(0, dp(5)));
        if (!detail.isBlank()) sheet.addView(text(detail, 13, MID), matchWrap(0, dp(14)));
        LinearLayout actions = new LinearLayout(this);
        actions.setGravity(Gravity.END);
        Button cancel = dialogActionButton("Cancel", false, BLACK);
        Button approve = dialogActionButton(DeveloperApprovalPolicy.primaryLabel(), true, BLACK);
        cancel.setOnClickListener(view -> { developerClient.respondToApproval(requestId, "decline"); dialog.dismiss(); });
        approve.setOnClickListener(view -> {
            developerClient.respondToApproval(requestId, DeveloperApprovalPolicy.approvalDecision());
            dialog.dismiss();
        });
        actions.addView(cancel, weightedDialogAction(0));
        actions.addView(approve, weightedDialogAction(dp(10)));
        sheet.addView(actions, matchWrap());
        dialog.setContentView(sheet); dialog.setCancelable(false); dialog.show();
        Window window = dialog.getWindow();
        if (window != null) {
            window.setBackgroundDrawableResource(android.R.color.transparent);
            window.setGravity(Gravity.BOTTOM);
            WindowManager.LayoutParams attributes = window.getAttributes();
            attributes.width = WindowManager.LayoutParams.MATCH_PARENT;
            attributes.horizontalMargin = 0.03f;
            attributes.verticalMargin = 0.02f;
            window.setAttributes(attributes);
        }
    }

    private void addDeveloperActivity(JSONObject item, boolean completed) {
        emptyState.setVisibility(View.GONE);
        String id = item.optString("id", item.optString("type") + System.nanoTime());
        String rawType = item.optString("type", "activity");
        TextView existing = developerActivityStatuses.get(id);
        String state = item.optString("status", completed ? "completed" : "inProgress");
        String status = DeveloperActivityPolicy.status(state, completed);
        if (existing != null) {
            existing.setText(status);
            View parent = (View) existing.getParent();
            if (parent != null && ("commandExecution".equals(rawType) || "fileChange".equals(rawType))) {
                parent.setOnClickListener(view -> showDeveloperActivityDetails(item));
            }
            scrollToBottom();
            return;
        }
        String title = DeveloperActivityPolicy.title(rawType);
        LinearLayout card = new LinearLayout(this);
        card.setOrientation(LinearLayout.VERTICAL);
        card.setPadding(dp(16), dp(13), dp(16), dp(13));
        card.setBackground(rounded(SOFT, 18, 1, LINE));
        TextView heading = text(title, 14, BLACK);
        heading.setTypeface(Typeface.create("sans-serif-medium", Typeface.NORMAL));
        TextView statusView = text(status, 13, MID);
        card.addView(heading, matchWrap(0, dp(3)));
        card.addView(statusView, matchWrap());
        if ("commandExecution".equals(rawType) || "fileChange".equals(rawType)) {
            TextView affordance = text("Tap for details", 12, MID);
            affordance.setPadding(0, dp(5), 0, 0);
            card.addView(affordance, matchWrap());
            card.setClickable(true);
            card.setOnClickListener(view -> showDeveloperActivityDetails(item));
        }
        developerActivityStatuses.put(id, statusView);
        LinearLayout.LayoutParams params = matchWrap(dp(6), dp(7));
        params.leftMargin = dp(4); params.rightMargin = dp(4);
        messageList.addView(card, params);
        scrollToBottom();
    }

    private void showDeveloperActivityDetails(JSONObject item) {
        Dialog dialog = new Dialog(this);
        LinearLayout sheet = new LinearLayout(this);
        sheet.setOrientation(LinearLayout.VERTICAL);
        sheet.setPadding(dp(20), dp(18), dp(20), dp(20));
        sheet.setBackground(rounded(WHITE, 26, 1, LINE));
        TextView heading = text(DeveloperActivityPolicy.title(item.optString("type")), 19, BLACK);
        heading.setTypeface(Typeface.create("sans-serif-medium", Typeface.NORMAL));
        sheet.addView(heading, matchWrap(0, dp(10)));
        ScrollView scroll = new ScrollView(this);
        TextView detail = text(DeveloperActivityPolicy.details(item), 12, BLACK);
        detail.setTypeface(Typeface.MONOSPACE);
        detail.setTextIsSelectable(true);
        detail.setPadding(dp(12), dp(12), dp(12), dp(12));
        detail.setBackground(rounded(SOFT, 14, 1, LINE));
        scroll.addView(detail, matchWrap());
        sheet.addView(scroll, new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, dp(420)));
        Button close = dialogActionButton("Close", true, BLACK);
        close.setOnClickListener(view -> dialog.dismiss());
        sheet.addView(close, matchWrap(dp(12), 0));
        dialog.setContentView(sheet); dialog.show();
        Window window = dialog.getWindow();
        if (window != null) { window.setBackgroundDrawableResource(android.R.color.transparent); window.setGravity(Gravity.BOTTOM); window.setLayout(WindowManager.LayoutParams.MATCH_PARENT, WindowManager.LayoutParams.WRAP_CONTENT); }
    }

    private void startDeveloperDictation() {
        Intent intent = new Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH)
            .putExtra(RecognizerIntent.EXTRA_LANGUAGE_MODEL, RecognizerIntent.LANGUAGE_MODEL_FREE_FORM)
            .putExtra(RecognizerIntent.EXTRA_PROMPT, "Developer instruction")
            .putExtra(RecognizerIntent.EXTRA_MAX_RESULTS, 1);
        try { startActivityForResult(intent, REQUEST_DEVELOPER_DICTATION); }
        catch (Exception exception) { Toast.makeText(this, "Voice dictation is unavailable", Toast.LENGTH_SHORT).show(); }
    }

    @Override protected void onActivityResult(int requestCode, int resultCode, Intent data) {
        super.onActivityResult(requestCode, resultCode, data);
        if (resultCode != RESULT_OK || data == null) return;
        if (requestCode == REQUEST_DEVELOPER_DICTATION) {
            ArrayList<String> results = data.getStringArrayListExtra(RecognizerIntent.EXTRA_RESULTS);
            if (results != null && !results.isEmpty()) {
                composer.setText(results.get(0)); composer.setSelection(composer.length()); composer.requestFocus();
            }
        } else if (requestCode == REQUEST_DEVELOPER_ATTACHMENT && data.getData() != null) {
            addDeveloperAttachment(data);
        }
    }

    private void addDeveloperAttachment(Intent data) {
        try {
            String mime = getContentResolver().getType(data.getData());
            if (mime == null) mime = "application/octet-stream";
            String name = "attachment";
            try (Cursor cursor = getContentResolver().query(data.getData(), null, null, null, null)) {
                if (cursor != null && cursor.moveToFirst()) {
                    int column = cursor.getColumnIndex(OpenableColumns.DISPLAY_NAME);
                    if (column >= 0) name = cursor.getString(column);
                }
            }
            ByteArrayOutputStream output = new ByteArrayOutputStream();
            try (InputStream input = getContentResolver().openInputStream(data.getData())) {
                if (input == null) throw new IllegalArgumentException("File could not be opened");
                byte[] buffer = new byte[16_384]; int read;
                while ((read = input.read(buffer)) >= 0) {
                    if (output.size() + read > MAX_DEVELOPER_ATTACHMENT_BYTES) throw new IllegalArgumentException("Attachment is larger than 1.5 MB");
                    output.write(buffer, 0, read);
                }
            }
            pendingDeveloperAttachments.put(new JSONObject().put("name", name).put("mime", mime)
                .put("data", Base64.encodeToString(output.toByteArray(), Base64.NO_WRAP)));
            statusText.setText(pendingDeveloperAttachments.length() + " attachment ready");
        } catch (Exception exception) {
            Toast.makeText(this, exception.getMessage(), Toast.LENGTH_LONG).show();
        }
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
        return true;
    }

    private void openHistory() {
        startActivity(
            new Intent(this, ChatHistoryActivity.class)
        );
    }

    private void newChat() {
        generating = false;
        finishStreaming();
        startForegroundService(
            new Intent(this, VoiceService.class)
                .setAction(VoiceService.ACTION_NEW_CHAT)
        );
    }


    private void confirmDeleteChat() {
        Dialog dialog = new Dialog(this);
        LinearLayout sheet = new LinearLayout(this);
        sheet.setOrientation(LinearLayout.VERTICAL);
        sheet.setPadding(dp(22), dp(20), dp(22), dp(18));
        sheet.setBackground(rounded(WHITE, 26, 1, LINE));
        TextView title = text("Clear this chat?", 20, BLACK);
        title.setTypeface(Typeface.create("sans-serif-medium", Typeface.NORMAL));
        sheet.addView(title, matchWrap(0, dp(7)));
        sheet.addView(text("Only the current conversation will be removed.", 14, MID), matchWrap(0, dp(18)));
        LinearLayout actions = new LinearLayout(this);
        actions.setGravity(Gravity.END);
        Button cancel = dialogActionButton("Cancel", false, BLACK);
        cancel.setOnClickListener(view -> dialog.dismiss());
        Button clear = dialogActionButton("Clear", true, Color.rgb(190, 36, 46));
        clear.setOnClickListener(view -> { dialog.dismiss(); deleteCurrentChat(); });
        actions.addView(cancel, weightedDialogAction(0));
        actions.addView(clear, weightedDialogAction(dp(10)));
        sheet.addView(actions, matchWrap());
        dialog.setContentView(sheet);
        Window window = dialog.getWindow();
        if (window != null) {
            window.setBackgroundDrawableResource(android.R.color.transparent);
            window.setDimAmount(0.22f); window.addFlags(WindowManager.LayoutParams.FLAG_DIM_BEHIND);
            window.setGravity(Gravity.BOTTOM);
            WindowManager.LayoutParams params = window.getAttributes();
            params.width = WindowManager.LayoutParams.MATCH_PARENT;
            params.horizontalMargin = 0.03f; params.verticalMargin = 0.02f;
            window.setAttributes(params);
        }
        dialog.show();
    }

    private void deleteCurrentChat() {
        generating = false;
        finishStreaming();

        startForegroundService(
            new Intent(this, VoiceService.class)
                .setAction(
                    VoiceService.ACTION_DELETE_CHAT
                )
        );

        Toast.makeText(
            this,
            "Chat deleted",
            Toast.LENGTH_SHORT
        ).show();
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
            TextView name = text(
                DeveloperRoutingPolicy.routesToDeveloper(assistantMode) ? "Developer" : "Jarvis",
                12,
                MID
            );
            name.setTypeface(Typeface.DEFAULT_BOLD);
            holder.addView(name, wrapWrap(0, dp(4)));
        }

        TextView content = text("", 16, BLACK);
        content.setText(
            ChatTextFormatter.format(message.text)
        );
        content.setTextIsSelectable(true);
        content.setMovementMethod(
            LinkMovementMethod.getInstance()
        );
        content.setLineSpacing(0f, 1.14f);
        content.setOnLongClickListener(view -> {
            showMessageActions(view, message);
            return true;
        });
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

        if (assistant) {
            TextView copy = text("Copy", 12, MID);
            copy.setPadding(0, dp(4), dp(8), dp(2));
            copy.setOnClickListener(
                view -> copyText(message.text)
            );
            holder.addView(copy, wrapWrap());
        }

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

        TextView name = text(
            DeveloperRoutingPolicy.routesToDeveloper(assistantMode) ? "Developer" : "Jarvis",
            12,
            MID
        );
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

    private void handleSendOrStop() {
        if (generating) {
            cancelGeneration();
        } else {
            sendMessage();
        }
    }

    private void cancelGeneration() {
        if (DeveloperRoutingPolicy.routesToDeveloper(assistantMode)) {
            developerClient.interrupt();
            return;
        }
        startService(
            new Intent(this, VoiceService.class)
                .setAction(
                    VoiceService.ACTION_CANCEL_RESPONSE
                )
        );
    }

    private void showMessageActions(
        View anchor,
        ChatMessage message
    ) {
        PopupMenu popup = new PopupMenu(this, anchor);
        popup.getMenu().add(0, 1, 0, "Copy");

        if (ChatMessage.USER.equals(message.role)) {
            popup.getMenu().add(
                0,
                2,
                1,
                "Edit and resend"
            );
        } else if (
            ChatMessage.ASSISTANT.equals(message.role)
        ) {
            popup.getMenu().add(
                0,
                3,
                1,
                "Retry answer"
            );
        }

        popup.getMenu().add(0, 4, 2, "Delete");
        popup.setOnMenuItemClickListener(item -> {
            return switch (item.getItemId()) {
                case 1 -> {
                    copyText(message.text);
                    yield true;
                }
                case 2 -> {
                    editMessage(message.text);
                    yield true;
                }
                case 3 -> {
                    retryMessage(message);
                    yield true;
                }
                case 4 -> {
                    history.deleteMessage(message.id);
                    renderHistory();
                    yield true;
                }
                default -> false;
            };
        });
        popup.show();
    }

    private void copyText(String value) {
        ClipboardManager clipboard =
            (ClipboardManager) getSystemService(
                Context.CLIPBOARD_SERVICE
            );
        if (clipboard == null) return;
        clipboard.setPrimaryClip(
            ClipData.newPlainText("Jarvis message", value)
        );
        Toast.makeText(
            this,
            "Copied",
            Toast.LENGTH_SHORT
        ).show();
    }

    private void editMessage(String value) {
        composer.setText(value);
        composer.setSelection(composer.length());
        composer.requestFocus();
    }

    private void retryMessage(ChatMessage message) {
        String userMessage = history.previousUserMessage(
            message.id
        );
        if (userMessage.isBlank()) {
            Toast.makeText(
                this,
                "No earlier user message to retry",
                Toast.LENGTH_SHORT
            ).show();
            return;
        }
        composer.setText(userMessage);
        composer.setSelection(composer.length());
        sendMessage();
    }

    private void scrollToBottom() {
        messageScroll.post(() -> messageScroll.fullScroll(View.FOCUS_DOWN));
    }

    private void updateMicButton() {
        if (micButton == null) return;
        if (DeveloperRoutingPolicy.routesToDeveloper(assistantMode)) {
            micButton.setImageResource(R.drawable.control_mic);
            micButton.setBackgroundColor(Color.TRANSPARENT);
            micButton.clearColorFilter();
            micButton.setContentDescription("Dictate developer instruction");
            micButton.setAlpha(1f);
            return;
        }
        if (voiceActive) {
            micButton.setImageResource(R.drawable.control_close);
            micButton.setBackgroundColor(Color.TRANSPARENT);
            micButton.clearColorFilter();
            micButton.setContentDescription(
                listening ? "Listening. Tap to stop voice." : "Stop voice"
            );
            micButton.setAlpha(1f);
        } else {
            micButton.setImageResource(R.drawable.control_mic);
            micButton.setBackgroundColor(Color.TRANSPARENT);
            micButton.clearColorFilter();
            micButton.setContentDescription("Start voice");
            micButton.setAlpha(1f);
        }
    }

    private void updateSendButton() {
        if (sendButton == null || composer == null) return;
        if (generating) {
            sendButton.setImageResource(R.drawable.ic_stop);
            sendButton.setContentDescription(
                "Stop generating"
            );
            sendButton.setEnabled(true);
            sendButton.setAlpha(1f);
            return;
        }

        sendButton.setImageResource(R.drawable.ic_send);
        sendButton.setContentDescription("Send message");
        boolean enabled =
            !composer.getText().toString().trim().isEmpty();
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

    private Button dialogActionButton(String label, boolean primary, int primaryColour) {
        Button button = new Button(this);
        button.setText(label);
        button.setTextSize(14);
        button.setTextColor(primary ? WHITE : BLACK);
        button.setTypeface(Typeface.create("sans-serif-medium", Typeface.NORMAL));
        button.setAllCaps(false);
        button.setMinWidth(0);
        button.setMinimumWidth(0);
        button.setMinHeight(0);
        button.setMinimumHeight(0);
        button.setPadding(dp(14), 0, dp(14), 0);
        button.setBackground(rounded(primary ? primaryColour : SOFT, 22, primary ? 0 : 1,
            primary ? Color.TRANSPARENT : LINE));
        return button;
    }

    private LinearLayout.LayoutParams weightedDialogAction(int leftMargin) {
        LinearLayout.LayoutParams params = new LinearLayout.LayoutParams(0, dp(48), 1f);
        params.leftMargin = leftMargin;
        return params;
    }

    private ImageButton assetButton(int icon, String description) {
        ImageButton button = new ImageButton(this);
        button.setImageResource(icon);
        button.setScaleType(android.widget.ImageView.ScaleType.CENTER_INSIDE);
        button.setAdjustViewBounds(true);
        button.setPadding(dp(2), dp(2), dp(2), dp(2));
        button.setBackgroundColor(Color.TRANSPARENT);
        button.setContentDescription(description);
        return button;
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
