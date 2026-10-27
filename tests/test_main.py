import sys
from types import SimpleNamespace

import pytest

import discord_personal_agent.__main__ as entry


@pytest.fixture
def run(monkeypatch, tmp_path):
    config = tmp_path / "config.toml"
    config.write_text('timezone = "Asia/Taipei"\n[allowed_channels]\n')
    monkeypatch.setenv("BOT_CONFIG_PATH", str(config))
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "token")
    monkeypatch.chdir(tmp_path)
    events = []

    class Agent:
        def __init__(self, settings, *, api_key):
            pass

        def run(self, token, log_handler):
            events.append("bot_run")

    class Voice:
        @classmethod
        def start(cls, directory):
            events.append(("voice_start", directory))
            return cls()

        def stop(self):
            events.append("voice_stop")

    monkeypatch.setattr(entry, "PersonalAgent", Agent)
    monkeypatch.setattr(entry, "VoiceProcess", Voice)

    def invoke(*args):
        monkeypatch.setattr(sys, "argv", ["discord_personal_agent", *args])
        entry.main()
        return events

    return SimpleNamespace(invoke=invoke, root=tmp_path, agent=Agent, events=events)


def test_starts_voice_from_voice_directory_and_stops_it_after_the_bot(run):
    assert run.invoke() == [("voice_start", run.root / "voice"), "bot_run", "voice_stop"]


def test_no_voice_runs_text_only(run):
    assert run.invoke("--no-voice") == ["bot_run"]


def test_voice_is_stopped_even_when_the_bot_fails(run, monkeypatch):
    import discord

    def fail(self, token, log_handler):
        raise discord.LoginFailure

    monkeypatch.setattr(run.agent, "run", fail)
    with pytest.raises(SystemExit):
        run.invoke()
    assert run.events[-1] == "voice_stop"
