from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest

from discord_personal_agent.conversation import Conversation, SourceMessage
from discord_personal_agent.privacy import PrivacyError, PrivacyGuard, Rule


def conversation(content, name="Owen"):
    return Conversation(
        111,
        222,
        1,
        (
            SourceMessage(
                1, 123, name, datetime(2026, 9, 11, 7, tzinfo=UTC), content, 0, False, None
            ),
        ),
    )


def test_builtin_masks_content_name_topic_and_url_credentials_without_mutating_input():
    original = conversation(
        "寄到 alice@example.com，https://example.com/path?token=secret-value", "alice@example.com"
    )
    result = PrivacyGuard().sanitize(original, topic="聯絡 0912-345-678")
    assert "alice@example.com" not in result.conversation.render(ZoneInfo("UTC"))
    assert "secret-value" not in result.conversation.messages[0].content
    assert "0912" not in result.topic
    assert "[REDACTED]" in result.conversation.messages[0].content
    assert result.conversation.messages[0].id == 1
    assert original.messages[0].display_name == "alice@example.com"


@pytest.mark.parametrize("location", ["content", "name", "topic"])
def test_block_takes_priority_over_mask_without_echoing_value(location):
    guard = PrivacyGuard(
        (
            Rule("mask-term", "literal", "內部專案"),
            Rule("block-term", "literal", "內部專案", "block"),
        )
    )
    original = conversation(
        "內部專案" if location == "content" else "普通對話",
        "內部專案" if location == "name" else "Owen",
    )
    with pytest.raises(PrivacyError) as error:
        guard.sanitize(original, topic="內部專案" if location == "topic" else "")
    assert "內部專案" not in str(error.value)


def test_overlapping_masks_remove_entire_original_matches():
    result = PrivacyGuard((Rule("a", "literal", "abc"), Rule("b", "literal", "cde"))).sanitize(
        conversation("abcdef")
    )
    assert result.conversation.messages[0].content == "[REDACTED]f"


@pytest.mark.parametrize(
    "text",
    [
        "Authorization: Bearer abcdef.123456",
        "sk-" + "a" * 24,
        "https://user:password@example.com/path",
        "https://example.com/?%74oken=sensitive",
    ],
)
def test_common_credentials_are_fully_replaced(text):
    result = PrivacyGuard().sanitize(conversation(text))
    assert "abcdef" not in result.conversation.messages[0].content
    assert "a" * 24 not in result.conversation.messages[0].content
    assert "password" not in result.conversation.messages[0].content
    assert "sensitive" not in result.conversation.messages[0].content
    assert "[REDACTED]" in result.conversation.messages[0].content


def test_expensive_regex_stops_without_returning_raw_content():
    guard = PrivacyGuard((Rule("expensive", "regex", "(a+)+$"),))
    with pytest.raises(PrivacyError, match="逾時"):
        guard.sanitize(conversation("a" * 100000 + "!"))


def test_block_in_last_message_does_not_return_earlier_masked_messages():
    from dataclasses import replace

    original = conversation("alice@example.com")
    original = replace(
        original,
        selected_count=2,
        messages=(
            original.messages[0],
            replace(original.messages[0], id=2, content="x" * 20000 + "禁止外送"),
        ),
    )
    guard = PrivacyGuard((Rule("block", "literal", "禁止外送", "block"),))
    with pytest.raises(PrivacyError):
        guard.sanitize(original)


def test_encoded_fragment_credential_masks_entire_url():
    result = PrivacyGuard().sanitize(conversation("https://example.com/#%74oken=secret-value"))
    assert result.conversation.messages[0].content == "[REDACTED]"
