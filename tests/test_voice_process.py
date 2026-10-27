import logging
import stat
import time

from discord_personal_agent.voice_process import BINARY, VoiceProcess


def fake_voice(tmp_path, body):
    binary = tmp_path / BINARY
    binary.parent.mkdir(parents=True)
    binary.write_text("#!/bin/sh\n" + body)
    binary.chmod(binary.stat().st_mode | stat.S_IXUSR)
    return binary


def wait_for(path):
    deadline = time.monotonic() + 5
    while not path.exists() and time.monotonic() < deadline:
        time.sleep(0.02)


def test_missing_binary_runs_text_only(tmp_path, caplog):
    assert VoiceProcess.start(tmp_path) is None
    assert "voice_disabled reason=binary_missing" in caplog.text


def test_unstartable_binary_runs_text_only(tmp_path, caplog):
    binary = fake_voice(tmp_path, "exit 0\n")
    binary.chmod(0o600)
    assert VoiceProcess.start(tmp_path) is None
    assert "voice_disabled reason=start_failed" in caplog.text


def test_starts_in_voice_directory_without_llm_key_and_waits_for_drain(tmp_path, monkeypatch):
    monkeypatch.setenv("SHARED_LLM_API_KEY", "groq-secret")
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "bot-token")
    fake_voice(
        tmp_path,
        # Mimic the Rust program: on SIGINT, take a while to drain, then exit.
        "trap 'sleep 0.3; echo drained > drained; exit 0' INT\n"
        'printf "%s|%s|%s" "$PWD" "$SHARED_LLM_API_KEY" "$DISCORD_BOT_TOKEN" > env\n'
        "while true; do sleep 0.05; done\n",
    )
    voice = VoiceProcess.start(tmp_path)
    assert voice is not None
    wait_for(tmp_path / "env")
    assert (tmp_path / "env").read_text() == f"{tmp_path}||bot-token"
    voice.stop()
    assert (tmp_path / "drained").read_text() == "drained\n"
    assert voice.process.returncode == 0


def test_stop_does_not_signal_an_already_exited_program(tmp_path, monkeypatch):
    fake_voice(tmp_path, "exit 3\n")
    voice = VoiceProcess.start(tmp_path)
    voice.process.wait()
    sent = []
    monkeypatch.setattr(voice.process, "send_signal", sent.append)
    voice.stop()
    assert sent == []
    assert voice.process.returncode == 3


def test_stop_keeps_waiting_through_a_second_ctrl_c(tmp_path, monkeypatch):
    fake_voice(
        tmp_path, "trap 'sleep 0.3; exit 0' INT\n: > ready\nwhile true; do sleep 0.05; done\n"
    )
    voice = VoiceProcess.start(tmp_path)
    wait_for(tmp_path / "ready")
    real_wait = voice.process.wait
    calls = []

    def interrupted_once(*args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise KeyboardInterrupt
        return real_wait(*args, **kwargs)

    monkeypatch.setattr(voice.process, "wait", interrupted_once)
    voice.stop()
    assert len(calls) == 2
    assert voice.process.returncode == 0


def test_unexpected_exit_is_a_warning_but_a_requested_stop_is_not(tmp_path, caplog):
    caplog.set_level(logging.INFO)
    fake_voice(tmp_path, "exit 3\n")
    crashed = VoiceProcess.start(tmp_path)
    crashed.process.wait()
    deadline = time.monotonic() + 5
    while "voice_exited" not in caplog.text and time.monotonic() < deadline:
        time.sleep(0.02)
    assert "WARNING" in caplog.text and "voice_exited_unexpectedly code=3" in caplog.text

    caplog.clear()
    (tmp_path / BINARY).write_text(
        "#!/bin/sh\ntrap 'exit 0' INT\n: > ready\nwhile true; do sleep 0.05; done\n"
    )
    stopped = VoiceProcess.start(tmp_path)
    wait_for(tmp_path / "ready")
    stopped.stop()
    deadline = time.monotonic() + 5
    while "voice_exited" not in caplog.text and time.monotonic() < deadline:
        time.sleep(0.02)
    assert "voice_exited code=0" in caplog.text
    assert "unexpectedly" not in caplog.text


def test_clean_exit_before_stop_is_not_reported_as_a_crash(tmp_path, caplog):
    # Ctrl+C reaches both programs; the voice program may finish draining first.
    caplog.set_level(logging.INFO)
    fake_voice(tmp_path, "exit 0\n")
    voice = VoiceProcess.start(tmp_path)
    voice.process.wait()
    deadline = time.monotonic() + 5
    while "voice_exited" not in caplog.text and time.monotonic() < deadline:
        time.sleep(0.02)
    assert "voice_exited code=0" in caplog.text
    assert "unexpectedly" not in caplog.text
