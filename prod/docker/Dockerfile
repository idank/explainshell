FROM caddy:2-builder AS caddy-builder
COPY prod/botshed /src/botshed
RUN xcaddy build --with explainshell.com/botshed=/src/botshed

# Fetch the DB in its own stage: wget/zstd/jq are needed only to download
# and unpack it, and would otherwise sit in the runtime image forever.
FROM python:3.12-slim AS db-builder

RUN apt-get update \
  && apt-get install -y --no-install-recommends wget zstd jq \
  && rm -rf /var/lib/apt/lists/*

WORKDIR /db
COPY tools/download-latest-db.sh tools/
# DB_NAME pins the release asset this image baked in. The deploy
# pipeline (CI workflow and `make deploy-local`) looks it up from the
# db-latest release and passes it here. It serves two roles:
#   1) cache bust — referenced in the RUN string, so a new DB in the
#      release invalidates this layer and forces re-download.
#   2) content pin — the script verifies sha256 against the release
#      metadata, so what lands in the image is exactly what CI captured
#      (not "whatever is newest at build time", which would race with
#      concurrent uploads).
ARG DB_NAME
RUN tools/download-latest-db.sh --asset "$DB_NAME" explainshell.db

FROM python:3.12-slim

COPY --from=caddy-builder /usr/bin/caddy /usr/local/bin/caddy

WORKDIR /opt/webapp
# requirements.txt is the web-serving set only. The LLM provider SDKs live
# in requirements-extraction.txt and are deliberately not installed here —
# the serving path imports none of them, and they cost ~65MB.
COPY requirements.txt .
RUN pip3 install --no-cache-dir --no-warn-script-location -r requirements.txt

# The .sha256 sidecar is read at startup and folded into the ETag
# (see explainshell/web/__init__.py), so it has to travel with the DB.
COPY --from=db-builder /db/explainshell.db /db/explainshell.db.sha256 ./

COPY prod/docker/Caddyfile /etc/caddy/Caddyfile
COPY prod/docker/start.sh .
COPY explainshell/ explainshell/

# Bake app-level runtime config into the image so `docker run <image>`
# locally is byte-identical to prod. Anything that differs per
# environment (secrets, DB build pin, GIT_SHA) stays out.
ENV DEBUG=false
ENV DB_PATH=/opt/webapp/explainshell.db

# Surfaced in cache ETags so a code-only deploy still invalidates.
# Passed in by the deploy pipeline (see the deploy workflows under
# .github/workflows/).
ARG GIT_SHA=unknown
ENV GIT_SHA=$GIT_SHA

EXPOSE 8080

CMD ["./start.sh"]
