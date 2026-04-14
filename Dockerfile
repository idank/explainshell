FROM python:3.12-slim AS python-deps

ENV VENV_PATH=/opt/venv
ENV PATH="${VENV_PATH}/bin:${PATH}"

WORKDIR /opt/build
COPY requirements.txt .
RUN python -m venv "${VENV_PATH}" \
  && pip install --no-cache-dir --upgrade pip \
  && pip install --no-cache-dir -r requirements.txt

FROM alpine:3.18 AS db

ARG DB_URL=https://github.com/idank/explainshell/releases/download/db-latest/explainshell.db.zst
ARG DB_CACHE_BUST=0

RUN apk add --no-cache curl zstd

WORKDIR /opt/db
RUN printf '%s\n' "${DB_CACHE_BUST}" > .cache-bust \
  && curl -fsSL -o explainshell.db.zst "$DB_URL" \
  && zstd -d --rm explainshell.db.zst

FROM python:3.12-slim AS runtime

ENV VENV_PATH=/opt/venv
ENV PATH="${VENV_PATH}/bin:${PATH}"
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV DB_PATH=/opt/webapp/explainshell.db

WORKDIR /opt/webapp
COPY --from=python-deps "${VENV_PATH}" "${VENV_PATH}"
COPY --from=db /opt/db/explainshell.db ./explainshell.db
COPY explainshell/ explainshell/
COPY --chmod=755 docker/docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh

EXPOSE 5000

CMD ["docker-entrypoint.sh"]
