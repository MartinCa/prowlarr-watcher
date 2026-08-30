import { Link, Outlet, createRootRoute } from "@tanstack/react-router";
import { Toaster } from "sonner";

export const Route = createRootRoute({ component: RootLayout });

function RootLayout() {
  return (
    <div className="min-h-screen">
      <nav className="bg-card flex h-12 items-center gap-8 border-b px-6">
        <span className="font-mono text-sm font-medium tracking-wide">
          PROWLARR<span className="text-muted-foreground">/</span>WATCHER
        </span>
        <Link
          to="/"
          className="text-muted-foreground [&.active]:text-foreground text-sm"
          activeOptions={{ exact: true }}
          activeProps={{ className: "active" }}
        >
          Queries
        </Link>
        <Link
          to="/settings"
          className="text-muted-foreground [&.active]:text-foreground text-sm"
          activeProps={{ className: "active" }}
        >
          Settings
        </Link>
      </nav>
      <main className="mx-auto max-w-4xl px-6 py-8">
        <Outlet />
      </main>
      <Toaster richColors position="bottom-right" />
    </div>
  );
}
