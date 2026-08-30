FROM docker.io/library/node:24-slim@sha256:ba849c60be29959425b8734d57b8b4b7d56f98edd9504c9af091d5281095a71e AS frontend-build

WORKDIR /frontend

RUN corepack enable

COPY frontend/package.json frontend/pnpm-lock.yaml frontend/.npmrc ./
# The GitHub Packages credential has to go in a *user-level* npmrc. pnpm 11
# ignores an _authToken in a project .npmrc when its value is an environment
# variable — the file is committed, so expanding secrets into it could leak
# them to an attacker-controlled registry. It warns and carries on unauthed,
# which surfaces much later as a 401 that reads like a missing token.
# Written and removed inside this one RUN so it never lands in a layer.
RUN --mount=type=secret,id=github_packages_token \
    export NPM_CONFIG_USERCONFIG=/tmp/npmrc && \
    echo "//npm.pkg.github.com/:_authToken=$(cat /run/secrets/github_packages_token)" \
      > "$NPM_CONFIG_USERCONFIG" && \
    pnpm install --frozen-lockfile; \
    status=$?; rm -f "$NPM_CONFIG_USERCONFIG"; exit "$status"

COPY frontend/ ./
RUN pnpm build

FROM docker.io/library/python:3.14-slim@sha256:ce40764625a4ff50df3548277632e7f96c4e77fe75fa848aae9885476e7df5a4

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py db.py prowlarr.py worker.py notifications.py callbacks.py scheduler.py routes.py ./
COPY --from=frontend-build /frontend/dist/ static/

VOLUME ["/data"]

ENV DATA_DIR=/data \
    PYTHONUNBUFFERED=1

EXPOSE 5000

CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "1", "--threads", "4", "--timeout", "120", "app:app"]
