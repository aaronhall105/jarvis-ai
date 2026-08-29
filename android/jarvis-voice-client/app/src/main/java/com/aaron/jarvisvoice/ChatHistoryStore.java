package com.aaron.jarvisvoice;

import android.content.Context;
import android.content.SharedPreferences;

import org.json.JSONArray;
import org.json.JSONObject;

import java.util.ArrayList;
import java.util.Comparator;
import java.util.HashSet;
import java.util.List;
import java.util.Set;
import java.util.UUID;

public final class ChatHistoryStore {
    public enum DurableAddResult {
        ADDED,
        ALREADY_PRESENT,
        FAILED;

        public boolean persisted() {
            return this != FAILED;
        }
    }

    private static final String PREFS = "jarvis_chat_history";
    private static final String LEGACY_MESSAGES = "messages_v1800";
    private static final String KEY_CONVERSATIONS =
        "conversations_v190070";
    private static final String KEY_ACTIVE =
        "active_conversation_v190070";
    private static final String KEY_MIGRATED =
        "conversation_migration_v190070";
    private static final int MAX_MESSAGES_PER_CONVERSATION = 300;
    private static final int MAX_CONVERSATIONS = 60;

    private final SharedPreferences preferences;
    private final SecureStore secureStore;

    public ChatHistoryStore(Context context) {
        Context app = context.getApplicationContext();
        preferences = app.getSharedPreferences(
            PREFS,
            Context.MODE_PRIVATE
        );
        secureStore = new SecureStore(app);
        migrateLegacyHistory();
        ensureActiveConversation();
    }

    public synchronized String ensureActiveConversation() {
        List<Record> records = loadRecords();
        Record active = resolveActive(records, true);
        saveRecords(records);
        secureStore.setConversationId(active.id);
        return active.id;
    }

    public synchronized String activeConversationId() {
        return ensureActiveConversation();
    }

    public synchronized String activeTitle() {
        List<Record> records = loadRecords();
        Record active = resolveActive(records, true);
        saveRecords(records);
        return active.title;
    }

    public synchronized List<ChatMessage> list() {
        List<Record> records = loadRecords();
        Record active = resolveActive(records, true);
        saveRecords(records);
        return new ArrayList<>(active.messages);
    }

    public synchronized void add(
        String role,
        String text
    ) {
        addInternal(
            "",
            role,
            text,
            false
        );
    }

    public synchronized boolean addDurable(
        String role,
        String text
    ) {
        return addInternal(
            "",
            role,
            text,
            true
        ).persisted();
    }

    public synchronized DurableAddResult addDurable(
        String messageId,
        String role,
        String text
    ) {
        return addInternal(
            messageId,
            role,
            text,
            true
        );
    }

    private DurableAddResult addInternal(
        String messageId,
        String role,
        String text,
        boolean durable
    ) {
        String cleaned =
            ConversationPolicy.clean(text);

        if (cleaned.isBlank()) {
            return DurableAddResult.ALREADY_PRESENT;
        }

        List<Record> records =
            loadRecords();

        Record active =
            resolveActive(
                records,
                true
            );

        String stableId =
            messageId == null
                ? ""
                : messageId.trim();

        if (!stableId.isEmpty()) {
            for (
                ChatMessage message
                    : active.messages
            ) {
                if (
                    stableId.equals(
                        message.id
                    )
                ) {
                    /*
                     * A previous attempt may have updated the
                     * SharedPreferences in-memory map even when
                     * its disk commit reported failure. Retry
                     * the synchronous disk commit before
                     * claiming durability.
                     */
                    if (
                        durable
                            && !saveRecords(
                                records,
                                true
                            )
                    ) {
                        return DurableAddResult.FAILED;
                    }

                    return DurableAddResult
                        .ALREADY_PRESENT;
                }
            }
        }

        long now =
            System.currentTimeMillis();

        ChatMessage message =
            stableId.isEmpty()
                ? new ChatMessage(
                    role,
                    cleaned,
                    now
                )
                : new ChatMessage(
                    stableId,
                    role,
                    cleaned,
                    now
                );

        active.messages.add(
            message
        );

        trimMessages(active);

        if (
            ChatMessage.USER.equals(role)
                && (
                    active.title.isBlank()
                        || "New chat".equals(
                            active.title
                        )
                )
        ) {
            active.title =
                ConversationPolicy
                    .titleFromText(
                        cleaned
                    );
        }

        active.updatedAt = now;

        boolean saved =
            saveRecords(
                records,
                durable
            );

        return saved
            ? DurableAddResult.ADDED
            : DurableAddResult.FAILED;
    }

    public synchronized String createConversation() {
        List<Record> records = loadRecords();
        String id = secureStore.newConversationId();
        long now = System.currentTimeMillis();
        records.add(new Record(
            id,
            "New chat",
            secureStore.userName(),
            now,
            now,
            false,
            new ArrayList<>()
        ));
        setActivePreference(id);
        prune(records, id);
        saveRecords(records);
        return id;
    }

    public synchronized boolean switchConversation(String id) {
        String candidate = ConversationPolicy.clean(id);
        if (candidate.isBlank()) return false;

        List<Record> records = loadRecords();
        Record record = find(records, candidate);
        if (
            record == null
                || !ConversationPolicy.sameOwner(
                    record.owner,
                    secureStore.userName()
                )
        ) {
            return false;
        }

        record.updatedAt = System.currentTimeMillis();
        setActivePreference(record.id);
        secureStore.setConversationId(record.id);
        saveRecords(records);
        return true;
    }

    public synchronized void rekeyActiveConversation(String newId) {
        String candidate = ConversationPolicy.clean(newId);
        if (candidate.isBlank()) return;

        List<Record> records = loadRecords();
        Record active = resolveActive(records, true);
        if (candidate.equals(active.id)) {
            secureStore.setConversationId(candidate);
            return;
        }

        Record existing = find(records, candidate);
        if (
            existing != null
                && ConversationPolicy.sameOwner(
                    existing.owner,
                    active.owner
                )
        ) {
            mergeMessages(existing, active);
            if (
                "New chat".equals(existing.title)
                    && !"New chat".equals(active.title)
            ) {
                existing.title = active.title;
            }
            existing.pinned = existing.pinned || active.pinned;
            existing.updatedAt = Math.max(
                existing.updatedAt,
                active.updatedAt
            );
            records.remove(active);
            active = existing;
        } else {
            active.id = candidate;
        }

        setActivePreference(candidate);
        secureStore.setConversationId(candidate);
        saveRecords(records);
    }

    public synchronized List<ChatConversation> conversations(
        String query
    ) {
        List<Record> records = loadRecords();
        String owner = secureStore.userName();
        List<ChatConversation> result = new ArrayList<>();

        for (Record record : records) {
            if (!ConversationPolicy.sameOwner(record.owner, owner)) {
                continue;
            }
            ChatConversation summary = summary(record);
            if (ConversationPolicy.matches(summary, query)) {
                result.add(summary);
            }
        }

        result.sort(
            Comparator
                .comparing((ChatConversation item) -> !item.pinned)
                .thenComparing(
                    Comparator.comparingLong(
                        (ChatConversation item) -> item.updatedAt
                    ).reversed()
                )
        );
        return result;
    }

    public synchronized boolean renameConversation(
        String id,
        String title
    ) {
        String cleaned = ConversationPolicy.titleFromText(title);
        List<Record> records = loadRecords();
        Record record = findOwned(records, id);
        if (record == null) return false;
        record.title = cleaned;
        record.updatedAt = System.currentTimeMillis();
        saveRecords(records);
        return true;
    }

    public synchronized boolean togglePinned(String id) {
        List<Record> records = loadRecords();
        Record record = findOwned(records, id);
        if (record == null) return false;
        record.pinned = !record.pinned;
        record.updatedAt = System.currentTimeMillis();
        saveRecords(records);
        return record.pinned;
    }

    public synchronized boolean deleteConversation(String id) {
        List<Record> records = loadRecords();
        Record record = findOwned(records, id);
        if (record == null) return false;

        boolean active = record.id.equals(
            preferences.getString(KEY_ACTIVE, "")
        );
        records.remove(record);

        if (active) {
            Record replacement = newestOwned(records);
            if (replacement == null) {
                String replacementId =
                    secureStore.newConversationId();
                long now = System.currentTimeMillis();
                replacement = new Record(
                    replacementId,
                    "New chat",
                    secureStore.userName(),
                    now,
                    now,
                    false,
                    new ArrayList<>()
                );
                records.add(replacement);
            }
            setActivePreference(replacement.id);
            secureStore.setConversationId(replacement.id);
        }

        saveRecords(records);
        return true;
    }

    public synchronized boolean deleteMessage(String messageId) {
        String candidate = ConversationPolicy.clean(messageId);
        if (candidate.isBlank()) return false;

        List<Record> records = loadRecords();
        Record active = resolveActive(records, true);
        boolean removed = active.messages.removeIf(
            message -> candidate.equals(message.id)
        );
        if (removed) {
            active.updatedAt = System.currentTimeMillis();
            saveRecords(records);
        }
        return removed;
    }

    public synchronized String previousUserMessage(
        String beforeMessageId
    ) {
        List<ChatMessage> messages = list();
        String lastUser = "";
        for (ChatMessage message : messages) {
            if (
                beforeMessageId != null
                    && beforeMessageId.equals(message.id)
            ) {
                break;
            }
            if (ChatMessage.USER.equals(message.role)) {
                lastUser = message.text;
            }
        }
        if (lastUser.isBlank()) {
            for (int i = messages.size() - 1; i >= 0; i--) {
                ChatMessage message = messages.get(i);
                if (ChatMessage.USER.equals(message.role)) {
                    return message.text;
                }
            }
        }
        return lastUser;
    }

    public synchronized void clear() {
        List<Record> records = loadRecords();
        Record active = resolveActive(records, true);
        active.messages.clear();
        active.title = "New chat";
        active.updatedAt = System.currentTimeMillis();
        saveRecords(records);
    }

    private void migrateLegacyHistory() {
        if (preferences.getBoolean(KEY_MIGRATED, false)) return;

        List<Record> records = loadRecords();
        if (!records.isEmpty()) {
            preferences.edit()
                .putBoolean(KEY_MIGRATED, true)
                .apply();
            return;
        }

        List<ChatMessage> legacy = readLegacyMessages();
        if (!legacy.isEmpty()) {
            String id = secureStore.conversationId();
            long created = legacy.get(0).createdAt > 0L
                ? legacy.get(0).createdAt
                : System.currentTimeMillis();
            long updated = legacy.get(legacy.size() - 1).createdAt;
            if (updated <= 0L) updated = System.currentTimeMillis();

            String title = "Previous conversation";
            for (ChatMessage message : legacy) {
                if (ChatMessage.USER.equals(message.role)) {
                    title = ConversationPolicy.titleFromText(
                        message.text
                    );
                    break;
                }
            }

            records.add(new Record(
                id,
                title,
                secureStore.userName(),
                created,
                updated,
                false,
                legacy
            ));
            setActivePreference(id);
            saveRecords(records);
        }

        preferences.edit()
            .putBoolean(KEY_MIGRATED, true)
            .apply();
    }

    private List<ChatMessage> readLegacyMessages() {
        List<ChatMessage> messages = new ArrayList<>();
        String raw = preferences.getString(
            LEGACY_MESSAGES,
            "[]"
        );
        try {
            JSONArray array = new JSONArray(raw);
            for (int i = 0; i < array.length(); i++) {
                JSONObject item = array.optJSONObject(i);
                if (item == null) continue;
                String text = item.optString("text", "").trim();
                if (text.isBlank()) continue;
                long createdAt = item.optLong(
                    "created_at",
                    System.currentTimeMillis()
                );
                messages.add(new ChatMessage(
                    "legacy-" + createdAt + "-" + i,
                    item.optString(
                        "role",
                        ChatMessage.SYSTEM
                    ),
                    text,
                    createdAt
                ));
            }
        } catch (Exception ignored) {
        }
        return messages;
    }

    private Record resolveActive(
        List<Record> records,
        boolean create
    ) {
        String owner = secureStore.userName();
        String activeId = preferences.getString(
            KEY_ACTIVE,
            ""
        );
        Record active = find(records, activeId);

        if (
            active != null
                && ConversationPolicy.sameOwner(
                    active.owner,
                    owner
                )
        ) {
            secureStore.setConversationId(active.id);
            return active;
        }

        String secureId = secureStore.conversationId();
        Record secureRecord = find(records, secureId);
        if (
            secureRecord != null
                && ConversationPolicy.sameOwner(
                    secureRecord.owner,
                    owner
                )
        ) {
            setActivePreference(secureRecord.id);
            return secureRecord;
        }

        Record newest = newestOwned(records);
        if (newest != null) {
            setActivePreference(newest.id);
            secureStore.setConversationId(newest.id);
            return newest;
        }

        if (!create) return null;

        String id = secureStore.newConversationId();
        long now = System.currentTimeMillis();
        Record created = new Record(
            id,
            "New chat",
            owner,
            now,
            now,
            false,
            new ArrayList<>()
        );
        records.add(created);
        setActivePreference(id);
        return created;
    }

    private Record newestOwned(List<Record> records) {
        String owner = secureStore.userName();
        return records.stream()
            .filter(record -> ConversationPolicy.sameOwner(
                record.owner,
                owner
            ))
            .max(Comparator.comparingLong(
                record -> record.updatedAt
            ))
            .orElse(null);
    }

    private Record findOwned(
        List<Record> records,
        String id
    ) {
        Record record = find(
            records,
            ConversationPolicy.clean(id)
        );
        if (
            record == null
                || !ConversationPolicy.sameOwner(
                    record.owner,
                    secureStore.userName()
                )
        ) {
            return null;
        }
        return record;
    }

    private static Record find(
        List<Record> records,
        String id
    ) {
        if (id == null || id.isBlank()) return null;
        for (Record record : records) {
            if (id.equals(record.id)) return record;
        }
        return null;
    }

    private static ChatConversation summary(Record record) {
        String preview = "";
        if (!record.messages.isEmpty()) {
            preview = record.messages
                .get(record.messages.size() - 1)
                .text;
            preview = preview.replace('\n', ' ').trim();
            if (preview.length() > 90) {
                preview = preview.substring(0, 89).trim() + "…";
            }
        }
        return new ChatConversation(
            record.id,
            record.title,
            record.owner,
            record.createdAt,
            record.updatedAt,
            record.pinned,
            preview,
            record.messages.size()
        );
    }

    private List<Record> loadRecords() {
        List<Record> records = new ArrayList<>();
        String raw = preferences.getString(
            KEY_CONVERSATIONS,
            "[]"
        );
        try {
            JSONArray array = new JSONArray(raw);
            for (int i = 0; i < array.length(); i++) {
                JSONObject item = array.optJSONObject(i);
                if (item == null) continue;
                String id = item.optString("id", "").trim();
                if (id.isBlank()) continue;

                List<ChatMessage> messages =
                    new ArrayList<>();
                JSONArray messageArray = item.optJSONArray(
                    "messages"
                );
                if (messageArray != null) {
                    for (
                        int index = 0;
                        index < messageArray.length();
                        index++
                    ) {
                        JSONObject message =
                            messageArray.optJSONObject(index);
                        if (message == null) continue;
                        String text = message.optString(
                            "text",
                            ""
                        ).trim();
                        if (text.isBlank()) continue;
                        long createdAt = message.optLong(
                            "created_at",
                            System.currentTimeMillis()
                        );
                        messages.add(new ChatMessage(
                            message.optString(
                                "id",
                                "message-"
                                    + UUID.randomUUID()
                            ),
                            message.optString(
                                "role",
                                ChatMessage.SYSTEM
                            ),
                            text,
                            createdAt
                        ));
                    }
                }

                records.add(new Record(
                    id,
                    item.optString("title", "New chat"),
                    item.optString("owner", "Aaron"),
                    item.optLong(
                        "created_at",
                        System.currentTimeMillis()
                    ),
                    item.optLong(
                        "updated_at",
                        System.currentTimeMillis()
                    ),
                    item.optBoolean("pinned", false),
                    messages
                ));
            }
        } catch (Exception ignored) {
        }
        return records;
    }

    private void saveRecords(
        List<Record> records
    ) {
        saveRecords(
            records,
            false
        );
    }

    private boolean saveRecords(
        List<Record> records,
        boolean durable
    ) {
        JSONArray array =
            new JSONArray();

        for (Record record : records) {
            try {
                JSONArray messages =
                    new JSONArray();

                for (
                    ChatMessage message
                        : record.messages
                ) {
                    messages.put(
                        new JSONObject()
                            .put(
                                "id",
                                message.id
                            )
                            .put(
                                "role",
                                message.role
                            )
                            .put(
                                "text",
                                message.text
                            )
                            .put(
                                "created_at",
                                message.createdAt
                            )
                    );
                }

                array.put(
                    new JSONObject()
                        .put(
                            "id",
                            record.id
                        )
                        .put(
                            "title",
                            record.title
                        )
                        .put(
                            "owner",
                            record.owner
                        )
                        .put(
                            "created_at",
                            record.createdAt
                        )
                        .put(
                            "updated_at",
                            record.updatedAt
                        )
                        .put(
                            "pinned",
                            record.pinned
                        )
                        .put(
                            "messages",
                            messages
                        )
                );

            } catch (Exception ignored) {
            }
        }

        SharedPreferences.Editor editor =
            preferences.edit()
                .putString(
                    KEY_CONVERSATIONS,
                    array.toString()
                );

        if (durable) {
            return editor.commit();
        }

        editor.apply();
        return true;
    }

    private void prune(
        List<Record> records,
        String activeId
    ) {
        while (records.size() > MAX_CONVERSATIONS) {
            Record oldest = records.stream()
                .filter(record -> !record.id.equals(activeId))
                .filter(record -> !record.pinned)
                .min(Comparator.comparingLong(
                    record -> record.updatedAt
                ))
                .orElse(null);
            if (oldest == null) {
                oldest = records.stream()
                    .filter(record -> !record.id.equals(activeId))
                    .min(Comparator.comparingLong(
                        record -> record.updatedAt
                    ))
                    .orElse(null);
            }
            if (oldest == null) break;
            records.remove(oldest);
        }
    }

    private static void trimMessages(Record record) {
        int remove = record.messages.size()
            - MAX_MESSAGES_PER_CONVERSATION;
        if (remove <= 0) return;
        record.messages.subList(0, remove).clear();
    }

    private static void mergeMessages(
        Record destination,
        Record source
    ) {
        Set<String> ids = new HashSet<>();
        for (ChatMessage message : destination.messages) {
            ids.add(message.id);
        }
        for (ChatMessage message : source.messages) {
            if (ids.add(message.id)) {
                destination.messages.add(message);
            }
        }
        destination.messages.sort(
            Comparator.comparingLong(
                message -> message.createdAt
            )
        );
        trimMessages(destination);
    }

    private void setActivePreference(String id) {
        preferences.edit()
            .putString(KEY_ACTIVE, id)
            .apply();
    }

    private static final class Record {
        String id;
        String title;
        final String owner;
        final long createdAt;
        long updatedAt;
        boolean pinned;
        final List<ChatMessage> messages;

        Record(
            String id,
            String title,
            String owner,
            long createdAt,
            long updatedAt,
            boolean pinned,
            List<ChatMessage> messages
        ) {
            this.id = id;
            this.title = title == null || title.isBlank()
                ? "New chat"
                : title;
            this.owner = owner == null ? "" : owner;
            this.createdAt = createdAt;
            this.updatedAt = updatedAt;
            this.pinned = pinned;
            this.messages = messages;
        }
    }
}
