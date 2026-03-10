import { Outlet } from "react-router-dom";
import { useUiStore } from "../stores/ui.ts";
import { useGatewayStore } from "../stores/gateway.ts";
import Topbar from "./Topbar.tsx";
import Sidebar from "./Sidebar.tsx";
import StatusBanner from "./StatusBanner.tsx";
import AICopilot from "./AICopilot.tsx";

export default function Layout() {
  const themeResolved = useUiStore((s) => s.themeResolved);
  const navCollapsed = useUiStore((s) => s.navCollapsed);
  const connected = useGatewayStore((s) => s.connected);

  const shellClasses = [
    "shell",
    navCollapsed ? "shell--nav-collapsed" : "",
  ]
    .filter(Boolean)
    .join(" ");

  return (
    <div
      className={shellClasses}
      data-theme={themeResolved}
      data-nav-collapsed={navCollapsed || undefined}
      data-connected={connected || undefined}
    >
      <Topbar />
      <Sidebar />
      <main className="content">
        <StatusBanner />
        <Outlet />
      </main>
      <AICopilot />
    </div>
  );
}
