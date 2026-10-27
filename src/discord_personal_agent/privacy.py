"""Local, bounded pattern matching. No original-value maps or network calls."""

from dataclasses import dataclass, field, replace
from time import monotonic
from urllib.parse import parse_qsl, unquote, urlsplit

import regex

from .conversation import Conversation


class PrivacyError(Exception):
    """Safe error that never contains matched text."""


@dataclass(frozen=True)
class Rule:
    id: str
    kind: str
    pattern: str = field(repr=False)
    action: str = "mask"


BUILTINS = (
    Rule("email", "regex", r"(?i)[\w.+%-]+@[\w.-]+\.[a-z]{2,}"),
    Rule("tw-mobile", "regex", r"(?<!\d)(?:09\d{2}|\+886[ -]?9\d{2})[ -]?\d{3}[ -]?\d{3}(?!\d)"),
    Rule(
        "key-prefix",
        "regex",
        r"\b(?:sk-[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9_]{16,}|mfa\.[A-Za-z0-9_-]{20,})",
    ),
    Rule("bearer", "regex", r"(?i)\bBearer\s+[A-Za-z0-9._~+/-]+=*"),
    Rule(
        "assignment",
        "regex",
        r"""(?i)\b(?:api[_-]?key|token|password|secret)\s*[:=]\s*(?:"[^"]+"|'[^']+'|[^\s&<>]+)""",
    ),
    Rule(
        "private-key",
        "regex",
        r"(?s)-----BEGIN (?:[A-Z0-9 ]+ )?PRIVATE KEY-----.*?"
        r"-----END (?:[A-Z0-9 ]+ )?PRIVATE KEY-----",
    ),
)
URL = regex.compile(r"""https?://[^\s<>"']+""", regex.IGNORECASE)
SECRET_QUERY_KEYS = {
    "token",
    "access_token",
    "refresh_token",
    "api_key",
    "apikey",
    "key",
    "password",
    "secret",
    "signature",
    "sig",
    "x-amz-signature",
    "x-goog-signature",
}


@dataclass(frozen=True)
class Sanitized:
    conversation: Conversation
    topic: str


class PrivacyGuard:
    def __init__(self, rules: tuple[Rule, ...] = ()):
        compiled = []
        ids = set()
        try:
            for rule in (*BUILTINS, *rules):
                if not regex.fullmatch(r"[a-zA-Z0-9_-]{1,64}", rule.id) or rule.id in ids:
                    raise ValueError
                ids.add(rule.id)
                if rule.kind not in {"literal", "regex"} or rule.action not in {"mask", "block"}:
                    raise ValueError
                if (
                    not isinstance(rule.pattern, str)
                    or not rule.pattern
                    or len(rule.pattern) > 4096
                ):
                    raise ValueError
                pattern = regex.escape(rule.pattern) if rule.kind == "literal" else rule.pattern
                compiled.append((regex.compile(pattern), rule.action))
            if len(rules) > 100:
                raise ValueError
        except (ValueError, TypeError, regex.error) as exc:
            raise ValueError("機密規則設定無效，請檢查 ID、種類、動作及 pattern。") from exc
        self._rules = tuple(compiled)

    def sanitize(self, conversation: Conversation, *, topic: str = "") -> Sanitized:
        deadline = monotonic() + 2.0
        fields = [topic]
        for message in conversation.messages:
            fields.extend((message.display_name, message.content))
        try:
            cleaned = [self._clean(text, deadline) for text in fields]
        except (TimeoutError, regex.error, ValueError) as exc:
            raise PrivacyError("機密檢查失敗或逾時，已停止輸出。") from exc
        messages = tuple(
            replace(message, display_name=cleaned[1 + 2 * i], content=cleaned[2 + 2 * i])
            for i, message in enumerate(conversation.messages)
        )
        return Sanitized(replace(conversation, messages=messages), cleaned[0])

    def _clean(self, text: str, deadline: float) -> str:
        spans = []
        for pattern, action in self._rules:
            budget = min(0.05, deadline - monotonic())
            if budget <= 0:
                raise TimeoutError
            for match in pattern.finditer(text, timeout=budget):
                if action == "block":
                    raise PrivacyError("內容命中管理員阻擋規則，已停止整次輸出。")
                if match.end() == match.start():
                    raise PrivacyError("機密規則出現空字串命中，已停止輸出。")
                spans.append(match.span())
        for match in URL.finditer(text, timeout=0.05):
            decoded = unquote(match.group())
            parsed = urlsplit(decoded)
            if parsed.username is not None or any(
                key.lower() in SECRET_QUERY_KEYS
                for key, _ in parse_qsl(parsed.query) + parse_qsl(parsed.fragment)
            ):
                spans.append(match.span())
        # Merge overlaps from the ORIGINAL text so earlier masking cannot hide later matches.
        merged = []
        for start, end in sorted(spans):
            if merged and start <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
            else:
                merged.append((start, end))
        pieces, cursor = [], 0
        for start, end in merged:
            pieces.extend((text[cursor:start], "[REDACTED]"))
            cursor = end
        pieces.append(text[cursor:])
        return "".join(pieces)
