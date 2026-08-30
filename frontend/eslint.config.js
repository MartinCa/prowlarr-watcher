import config from "@martinca/frontend-config/eslint";

export default [
  ...config(),
  {
    // TanStack Router file-based routes always export both `Route` and the
    // page component from the same file — that's the convention, not a bug.
    files: ["src/routes/**"],
    rules: { "react-refresh/only-export-components": "off" },
  },
  {
    // theme-provider.tsx (vendored from frontend-kit) exports both
    // ThemeProvider and useTheme. Drop this once @martinca/frontend-config
    // picks up the same exemption (frontend-kit PR #15) and we bump past it.
    files: ["src/components/theme-provider.tsx"],
    rules: { "react-refresh/only-export-components": "off" },
  },
];
