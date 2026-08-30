# Authenticating to GitHub Packages (`@martinca/*`)

`@martinca/frontend-config` (and any other `@martinca/*` scoped package) is
published to GitHub Packages (`npm.pkg.github.com`), not the public npm
registry. GitHub Packages requires an authenticated token for **every**
install — public or private, there is no anonymous-read mode for npm (unlike
the container registry).

`frontend/.npmrc` (checked into the repo, contains no secret — it only
references an env var by name) is already set up for this:

```
@martinca:registry=https://npm.pkg.github.com
//npm.pkg.github.com/:_authToken=${GITHUB_PACKAGES_TOKEN}
```

Both local and cloud setups below just need to get a `GITHUB_PACKAGES_TOKEN`
env var into the process environment before running `pnpm install`/`pnpm add`.
The `.npmrc` itself never changes.

## Local machine / Claude Code CLI

Prefer pulling the token live from `gh`'s own credential store rather than
minting and storing a separate PAT.

1. Make sure your `gh` auth includes the `read:packages` scope:
   ```sh
   gh auth status
   ```
   If `read:packages` isn't listed, add it without creating a new token:
   ```sh
   gh auth refresh -s read:packages
   ```
2. Export `GITHUB_PACKAGES_TOKEN` from `gh` in your shell profile
   (`~/.zshrc`, `~/.bashrc`, etc.) so it's set in every new shell:
   ```sh
   export GITHUB_PACKAGES_TOKEN="$(gh auth token)"
   ```
   This re-reads the current `gh` token each time a new shell starts — nothing
   is written to disk in plaintext, and there's no separate token to rotate or
   revoke later.

Never run `export GITHUB_PACKAGES_TOKEN=ghp_...` with a literal token pasted
inline — it lands in shell history and, in an agent session, in the
conversation transcript and tool logs.

## Cloud / Claude Code remote environment

Set `GITHUB_PACKAGES_TOKEN` as an environment variable on the environment
itself (its secrets/env-var settings), not by pasting the value into a chat
message — a value typed into a session transcript should be treated as
exposed and the underlying token rotated afterward.

**What was done for this environment:** a classic PAT scoped to
`read:packages` was added as `GITHUB_PACKAGES_TOKEN` to the cloud
environment's own env-var configuration (outside the chat). Because that
particular token had briefly appeared in plaintext in a chat message before
being moved into env-var storage, it should be treated as compromised —
rotate/revoke it on GitHub and replace the environment's `GITHUB_PACKAGES_TOKEN`
with a freshly generated one when convenient.

## Verifying it works

```sh
cd frontend
pnpm add -D @martinca/frontend-config
```

A `401 Unauthorized` means `GITHUB_PACKAGES_TOKEN` isn't set or lacks
`read:packages`; a `404 Not Found` on the package itself (rather than the
registry root) means the scope/registry mapping in `.npmrc` is wrong.
