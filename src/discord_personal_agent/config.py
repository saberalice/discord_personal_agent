"""Local configuration; never include input values in errors."""

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .privacy import PrivacyGuard, Rule


@dataclass(frozen=True)
class LlmSettings:
    max_input_tokens: int = 4000
    max_output_tokens: int = 4000

    def __post_init__(self):
        if any(
            type(value) is not int or value <= 0
            for value in (self.max_input_tokens, self.max_output_tokens)
        ):
            raise ValueError("LLM token 上限須為正整數。")


@dataclass(frozen=True)
class Settings:
    timezone: ZoneInfo
    allowed_channels: dict[int, frozenset[int]]
    privacy: PrivacyGuard = field(default_factory=PrivacyGuard, repr=False)
    llm: LlmSettings = field(default_factory=LlmSettings)


def load_settings(path: Path) -> Settings:
    try:
        with path.open("rb") as stream:
            data = tomllib.load(stream)
        if set(data) - {"timezone", "allowed_channels", "privacy_rules", "llm"}:
            raise ValueError
        timezone = ZoneInfo(data["timezone"])
        allowed = data["allowed_channels"]
        if not isinstance(allowed, dict):
            raise ValueError
        result = {}
        for guild, channels in allowed.items():
            if not isinstance(channels, list):
                raise ValueError
            for identifier in [guild, *channels]:
                if not isinstance(identifier, str) or not identifier.isascii():
                    raise ValueError
                if not identifier.isdecimal() or not 0 < int(identifier) < 2**64:
                    raise ValueError
            result[int(guild)] = frozenset(map(int, channels))
        raw_rules = data.get("privacy_rules", [])
        if not isinstance(raw_rules, list) or len(raw_rules) > 100:
            raise ValueError
        rules = []
        for item in raw_rules:
            if not isinstance(item, dict) or set(item) != {"id", "kind", "pattern", "action"}:
                raise ValueError
            if not all(isinstance(value, str) for value in item.values()):
                raise ValueError
            rules.append(Rule(**item))
        llm = LlmSettings(**data.get("llm", {}))
        return Settings(timezone, result, PrivacyGuard(tuple(rules)), llm)
    except (OSError, ValueError, TypeError, KeyError, ZoneInfoNotFoundError) as exc:
        raise ValueError("設定檔無法讀取或格式不正確，請參考 config.example.toml。") from exc
