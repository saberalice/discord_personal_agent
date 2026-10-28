#!/bin/sh
# Models live in a volume: fetch and verify them once, then start the bot (which starts voice).
set -eu
cd /app
# A .env saved by a Windows editor may carry CR line endings; the voice program uses the token
# exactly as given, so strip CR here rather than rely on how compose parses env_file.
for name in DISCORD_BOT_TOKEN SHARED_LLM_API_KEY; do
    eval "value=\${$name:-}"
    export "$name=$(printf '%s' "$value" | tr -d '\r')"
done
case " $* " in
    *" --no-voice "* | *" --check-config "*) ;;
    *)
        # Hashing 4.7 GB on every start is slow, so the marker records which manifest was last
        # fully verified. A changed manifest or a missing file runs the verifying download again.
        stamp=$(cat voice/scripts/qwen-manifest.json voice/scripts/download-qwen.py | sha256sum)
        if [ "$(cat voice/models/.verified 2>/dev/null)" != "$stamp" ] \
            || [ ! -f voice/models/qwen3-asr-gguf/Qwen3-ASR-1.7B-bf16.gguf ] \
            || [ ! -f voice/models/qwen3-asr-gguf/mmproj-Qwen3-ASR-1.7B-bf16.gguf ] \
            || [ ! -f voice/models/silero_vad.onnx ]; then
            rm -f voice/models/.verified
            if ! python3 voice/scripts/download-qwen.py; then
                # A .part that failed its checksum would fail every restart; drop it so the
                # next start fetches that file again.
                find voice/models -name '*.part' -delete
                exit 1
            fi
            printf '%s' "$stamp" > voice/models/.verified
        fi
        ;;
esac
exec /opt/venv/bin/python -m discord_personal_agent "$@"
