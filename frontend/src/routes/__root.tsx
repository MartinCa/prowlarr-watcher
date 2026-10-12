import { Link, Outlet, createRootRoute } from "@tanstack/react-router";
import { Toaster } from "@/components/ui/toast";
import { ThemeToggle } from "@/components/theme-toggle";

export const Route = createRootRoute({ component: RootLayout });

function RootLayout() {
  return (
    <div className="min-h-screen">
      <nav className="bg-card flex h-12 items-center gap-8 border-b px-6">
        <span className="flex items-center gap-2 font-mono text-sm font-medium tracking-wide">
          <img src="/favicon.svg" alt="" className="size-6" />
          <span>
            PROWLARR<span className="text-muted-foreground">/</span>WATCHER
          </span>
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
        <div className="flex-1" />
        <ThemeToggle />
      </nav>
      <main className="mx-auto max-w-4xl px-6 py-8">
        <Outlet />
      </main>
      <Toaster />
    </div>
  );
}
