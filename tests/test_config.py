from zoneinfo import ZoneInfo

import pytest

from discord_personal_agent.config import load_settings


def test_load_config_with_custom_timezone(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('timezone="Europe/London"\n[allowed_channels]\n"111"=["222"]\n')
    settings = load_settings(path)
    assert settings.timezone == ZoneInfo("Europe/London")
    assert settings.allowed_channels == {111: frozenset({222})}


@pytest.mark.parametrize(
    "content",
    [
        'timezone="Mars/Base"',
        'timezone="Asia/Taipei"\n[allowed_channels]\n"x"=["222"]',
        'timezone="Asia/Taipei"\n[allowed_channels]\n"111"="222"',
        "timezone=",
    ],
)
def test_bad_config_fails_without_echoing_values(tmp_path, content):
    path = tmp_path / "config.toml"
    path.write_text(content)
    with pytest.raises(ValueError, match="設定檔"):
        load_settings(path)


def test_empty_allowlist_starts_with_no_channels_exposed(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('timezone="Asia/Taipei"\n[allowed_channels]\n"111"=[]\n')
    assert load_settings(path).allowed_channels == {111: frozenset()}


def test_custom_privacy_rules_load_from_shared_config(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text("""timezone="Asia/Taipei"
[allowed_channels]
"111"=["222"]
[[privacy_rules]]
id="project"
kind="literal"
pattern="虛構專案"
action="block"
""")
    settings = load_settings(path)
    assert settings.privacy is not None


@pytest.mark.parametrize("pattern", ["[", ""])
def test_invalid_privacy_regex_rejected_at_startup(tmp_path, pattern):
    path = tmp_path / "config.toml"
    path.write_text(
        'timezone="Asia/Taipei"\n[allowed_channels]\n"111"=["222"]\n'
        '[[privacy_rules]]\nid="test"\nkind="regex"\naction="mask"\n'
        f'pattern="{pattern}"\n'
    )
    with pytest.raises(ValueError, match="設定檔"):
        load_settings(path)


def test_llm_limits_are_configurable_but_bounded(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(
        'timezone="Asia/Taipei"\n[allowed_channels]\n[llm]\nmax_input_tokens=3000\nmax_output_tokens=2000\n'
    )
    settings = load_settings(path)
    assert settings.llm.max_input_tokens == 3000
    assert settings.llm.max_output_tokens == 2000
    path.write_text('timezone="Asia/Taipei"\n[allowed_channels]\n[llm]\nmax_input_tokens=true\n')
    with pytest.raises(ValueError):
        load_settings(path)
