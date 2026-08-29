from pathlib import Path


CHAT = Path(__file__).parents[1] / "app" / "static" / "chat.html"


def test_multi_chat_uses_distinct_core_conversation_ids() -> None:
    source = CHAT.read_text(encoding="utf-8")
    assert 'class="chat-tabs"' in source
    assert "function createChat()" in source
    assert "conversation_id: chatId" in source
    assert "state.activeId = tab.dataset.chatId" in source


def test_multi_chat_renders_server_content_as_text_not_html() -> None:
    source = CHAT.read_text(encoding="utf-8")
    assert "bubble.textContent = text" in source
    assert "heading.textContent =" in source
    assert "localStorage.setItem(storageKey" in source
