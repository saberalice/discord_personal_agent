"""Run the Rust voice program beside the bot; it owns recording and must drain on shutdown."""

import logging
import os
import signal
import subprocess
import threading
from pathlib import Path

logger = logging.getLogger(__name__)
BINARY = Path("target/release/discord-asr-bot")
# The voice program never needs these. An empty value also stops its dotenvy from loading
# the real value out of the shared .env, so llama-server cannot inherit it either.
WITHHELD = ("SHARED_LLM_API_KEY",)


class VoiceProcess:
    def __init__(self, process: subprocess.Popen):
        self.process = process
        self.stopping = False

    @classmethod
    def start(cls, directory: Path) -> "VoiceProcess | None":
        binary = directory / BINARY
        if not binary.is_file():
            logger.warning("voice_disabled reason=binary_missing")
            return None
        try:
            # Same process group on purpose: a terminal Ctrl+C reaches both programs at once.
            process = subprocess.Popen(
                [str(binary.resolve())],
                cwd=directory,
                env=os.environ | dict.fromkeys(WITHHELD, ""),
                stdin=subprocess.DEVNULL,
            )
        except OSError as exc:
            logger.warning("voice_disabled reason=start_failed type=%s", type(exc).__name__)
            return None
        voice = cls(process)
        threading.Thread(target=voice._report_exit, daemon=True).start()
        logger.info("voice_started pid=%d", process.pid)
        return voice

    def _report_exit(self) -> None:
        code = self.process.wait()
        # A terminal Ctrl+C can reach the voice program before stop() runs; it then exits 0.
        if self.stopping or code == 0:
            logger.info("voice_exited code=%s", code)
        else:
            # Text features keep running, but recording is gone until the next restart.
            logger.warning("voice_exited_unexpectedly code=%s", code)

    def stop(self) -> None:
        """Ask the voice program to finish its ASR queue and wait; never kill it early."""
        self.stopping = True
        if self.process.poll() is None:
            self.process.send_signal(signal.SIGINT)
        while True:
            try:
                self.process.wait()
                return
            except KeyboardInterrupt:
                print("語音程式正在保存錄音並完成辨識佇列，請稍候。", flush=True)
