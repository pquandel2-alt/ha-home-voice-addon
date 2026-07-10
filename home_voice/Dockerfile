ARG BUILD_FROM
FROM $BUILD_FROM

# Build-Toolchain für llama.cpp + Python-Runtime für unseren aiohttp-Server.
# libcurl wird für den direkten HuggingFace-Download (--hf-repo/--hf-file) gebraucht.
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential cmake git ca-certificates \
        libcurl4-openssl-dev libgomp1 \
        python3 python3-aiohttp \
    && rm -rf /var/lib/apt/lists/*

# llama.cpp bauen — GGML_NATIVE=OFF + AVX2=ON: portabel, aber für den
# Ziel-Mini-PC (Intel i5-12450H, AVX2 vorhanden) optimiert. Kein -march=native,
# damit das Image auf dem HA-Build-Host reproduzierbar bleibt.
RUN git clone --depth 1 https://github.com/ggml-org/llama.cpp /tmp/llama.cpp \
    && cmake -S /tmp/llama.cpp -B /tmp/llama.cpp/build \
        -DCMAKE_BUILD_TYPE=Release \
        -DGGML_NATIVE=OFF -DGGML_AVX2=ON \
        -DLLAMA_CURL=ON \
        -DLLAMA_BUILD_TESTS=OFF -DLLAMA_BUILD_EXAMPLES=OFF \
    && cmake --build /tmp/llama.cpp/build --config Release -j --target llama-server \
    && cp /tmp/llama.cpp/build/bin/llama-server /usr/local/bin/llama-server \
    && rm -rf /tmp/llama.cpp

# App kopieren
COPY run.sh /run.sh
COPY server.py /server.py
COPY www /www
RUN chmod a+x /run.sh

CMD [ "/run.sh" ]
