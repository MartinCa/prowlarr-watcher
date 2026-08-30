# AGENTS.md

## Before writing any UI code

Read `DESIGN.md` in this repository in full. It is binding. If a rule there
conflicts with a habit, a tutorial, or a suggestion from a model, the file wins.

`DESIGN.md` is distributed from `MartinCa/frontend-kit` and is **not edited here**.
To change a convention, change it upstream and reinstall:

```sh
pnpm dlx shadcn@latest add MartinCa/frontend-kit/conventions --overwrite
```

The project-specific section at the bottom of `DESIGN.md` is the exception — that
part is owned by this repo.

## Shortcuts

- `shadcn info` — what is installed, which base, where the docs are.
- `shadcn docs <component>` — current API for a primitive. Use this instead of
  recalling props from memory; the Base UI and Radix APIs differ.
- `shadcn add <name> --dry-run` / `--view` — inspect before writing files.

## House rules that are linted

`pnpm lint` enforces the mechanical parts of `DESIGN.md`: no `any`, no deep
relative imports, no direct primitive imports outside `components/ui/`, no
inline `style` props, no fetching inside a Zustand store. If a rule fires,
fix the code rather than disabling the rule. If the rule is genuinely wrong,
say so and change it upstream in `@martinca/frontend-config`.

## Do not

- Add a state, data-fetching, or UI library. The stack is decided in `DESIGN.md`.
- Hand-edit `src/components/ui/**` or `src/lib/api-types.ts`. Both are vendored.
- Refactor files unrelated to the task in hand.
- Write a response interface by hand. Regenerate from the OpenAPI spec.
