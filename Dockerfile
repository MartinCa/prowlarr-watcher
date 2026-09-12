FROM docker.io/library/node:24-slim@sha256:2fe369e969550cde8e867afc3fe370b260140cab4a23d467074295b42163d553 AS frontend-build

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

FROM docker.io/library/python:3.14-slim@sha256:cad9a2c871761c413caa6fdd6441c783451e740a48aaeba60ae62a8b53525ef6

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
