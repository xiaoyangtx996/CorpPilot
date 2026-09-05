FROM node:22-bookworm-slim
RUN apt-get update && apt-get install -y --no-install-recommends python3 git ca-certificates && rm -rf /var/lib/apt/lists/* \
    && npm install --global @openai/codex@0.153.3 && npm cache clean --force
COPY entrypoint.py /opt/corppilot/entrypoint.py
USER 1000:1000
WORKDIR /work
ENTRYPOINT ["/usr/bin/python3", "/opt/corppilot/entrypoint.py"]
