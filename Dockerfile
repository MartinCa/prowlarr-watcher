FROM docker.io/library/node:24-slim@sha256:55fb5f5a527ea2c5c4a95f963e48ef90aa38c7b346054fdc8ee9957a2b55d205 AS frontend-build

WORKDIR /frontend

RUN corepack enable

# pnpm-workspace.yaml carries the supply-chain settings (trustLockfile,
# minimumReleaseAgeExclude). It has to be in this layer, not just in the
# repo — without it pnpm 11 applies its default minimumReleaseAge here and
# rejects any lockfile entry published in the last day.
COPY frontend/package.json frontend/pnpm-lock.yaml frontend/pnpm-workspace.yaml ./
# --ignore-scripts: this stage only needs the packages on disk to run `pnpm
# build` below, never a package's own install script. Without it, lefthook's
# `prepare` script (`lefthook install`) shells out to `git rev-parse` to find
# the repo root - which fails outright here, since this image has no `git`
# binary and the build context never copies `.git` in the first place.
RUN pnpm install --frozen-lockfile --ignore-scripts

COPY frontend/ ./
RUN pnpm build

FROM docker.io/library/python:3.15-slim@sha256:2da676046630d7bf14c6764ae1143dc7fa0377a53f94bf5a642f0b214aca5bce

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
