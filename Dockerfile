# Python bot + Rust voice program in one image. Models and recordings live in volumes, never in
# the image; secrets arrive as environment variables at run time (compose.yaml).
ARG CUDA_VERSION=12.9.1

FROM nvidia/cuda:${CUDA_VERSION}-devel-ubuntu24.04 AS voice-build
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential cmake pkg-config libssl-dev curl ca-certificates python3 \
    && rm -rf /var/lib/apt/lists/*
# Same toolchain the voice program was verified with on the development machine.
RUN curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs \
        | sh -s -- -y --profile minimal --default-toolchain 1.99.1
ENV PATH=/root/.cargo/bin:$PATH
# llama-server records this absolute library path (RUNPATH), so the runtime image keeps it.
WORKDIR /app/voice
COPY voice/scripts/build-engine.py scripts/build-engine.py
# Same pinned source and checksum as scripts/build-engine.py, but configured here: its default
# GGML_NATIVE probes the build machine's GPU, and `docker build` has no GPU to probe.
# GGML_NATIVE=OFF builds llama.cpp's default set of GPU architectures instead. -j 4 as in
# build-engine.py: multi-architecture CUDA compiles can exhaust Docker Desktop's memory cap.
RUN set -eu; \
    revision=$(sed -n "s/^REVISION = '\(.*\)'$/\1/p" scripts/build-engine.py); \
    sha=$(sed -n "s/^SHA256 = '\(.*\)'$/\1/p" scripts/build-engine.py); \
    curl -fL --retry 3 "https://github.com/ggml-org/llama.cpp/archive/$revision.tar.gz" -o /tmp/llama.tar.gz; \
    echo "$sha  /tmp/llama.tar.gz" | sha256sum -c -; \
    mkdir -p vendor; tar -xzf /tmp/llama.tar.gz -C vendor; rm /tmp/llama.tar.gz; \
    mv "vendor/llama.cpp-$revision" vendor/llama.cpp; \
    cmake -S vendor/llama.cpp -B vendor/llama.cpp/build -DCMAKE_BUILD_TYPE=Release \
        -DLLAMA_BUILD_TESTS=OFF -DLLAMA_BUILD_EXAMPLES=OFF \
        -DGGML_CUDA=ON -DGGML_METAL=OFF -DGGML_VULKAN=OFF -DGGML_NATIVE=OFF; \
    cmake --build vendor/llama.cpp/build --config Release -j 4 --target llama-server
COPY voice/Cargo.toml voice/Cargo.lock ./
COPY voice/src src
RUN cargo build --release --locked

FROM nvidia/cuda:${CUDA_VERSION}-runtime-ubuntu24.04
# Ubuntu 24.04's python3 is 3.12, which pyproject.toml requires; curl downloads the models;
# sqlite3 is for deleting a single recording session (docs/docker.md).
RUN apt-get update && apt-get install -y --no-install-recommends \
        python3 libgomp1 libssl3t64 curl ca-certificates sqlite3 \
    && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:0.11.18 /uv /bin/uv
ENV UV_PROJECT_ENVIRONMENT=/opt/venv UV_PYTHON=/usr/bin/python3 UV_PYTHON_DOWNLOADS=never \
    UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PYTHONUNBUFFERED=1
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project
COPY src src
RUN uv sync --locked --no-dev --no-editable
COPY --from=voice-build /app/voice/vendor/llama.cpp/build/bin voice/vendor/llama.cpp/build/bin
COPY --from=voice-build /app/voice/target/release/discord-asr-bot voice/target/release/discord-asr-bot
COPY voice/scripts voice/scripts
COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh
# Ubuntu 24.04 images already ship user `ubuntu` with UID 1000; replace it rather than clash.
RUN if id ubuntu >/dev/null 2>&1; then userdel --remove ubuntu; fi \
    && useradd --create-home --uid 1000 bot \
    && mkdir -p voice/models voice/data && chown bot:bot voice/models voice/data
USER bot
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
