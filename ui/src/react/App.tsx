import { useEffect } from "react";
import { Routes, Route, Navigate, useLocation } from "react-router-dom";
import Layout from "./components/Layout.tsx";
import ErrorBoundary from "./components/ErrorBoundary.tsx";
import { useAuthStore } from "./stores/auth.ts";
import { useUiStore } from "./stores/ui.ts";
import { useGateway } from "./hooks/useGateway.ts";
import { useTheme } from "./hooks/useTheme.ts";

import { lazy, Suspense } from "react";

const ChatPage = lazy(() => import("./pages/ChatPage.tsx"));
const OverviewPage = lazy(() => import("./pages/OverviewPage.tsx"));
const ChannelsPage = lazy(() => import("./pages/ChannelsPage.tsx"));
const InstancesPage = lazy(() => import("./pages/InstancesPage.tsx"));
const SessionsPage = lazy(() => import("./pages/SessionsPage.tsx"));
const UsagePage = lazy(() => import("./pages/UsagePage.tsx"));
const CronPage = lazy(() => import("./pages/CronPage.tsx"));
const AgentsPage = lazy(() => import("./pages/AgentsPage.tsx"));
const SkillsPage = lazy(() => import("./pages/SkillsPage.tsx"));
const NodesPage = lazy(() => import("./pages/NodesPage.tsx"));
const ConfigPage = lazy(() => import("./pages/ConfigPage.tsx"));
const DebugPage = lazy(() => import("./pages/DebugPage.tsx"));
const LogsPage = lazy(() => import("./pages/LogsPage.tsx"));
const OrgPage = lazy(() => import("./pages/OrgPage.tsx"));
const OrgChatPage = lazy(() => import("./pages/OrgChatPage.tsx"));
const LoginPage = lazy(() => import("./pages/LoginPage.tsx"));
const SignupPage = lazy(() => import("./pages/SignupPage.tsx"));

function PageSpinner() {
  return (
    <div className="page-spinner" role="status" aria-label="Loading page">
      <div className="spinner" />
    </div>
  );
}

function AuthGate({ children }: { children: React.ReactNode }) {
  const authState = useAuthStore((s) => s.authState);
  const authView = useAuthStore((s) => s.authView);

  if (authState === "checking") {
    return (
      <div className="auth-page">
        <div className="auth-card">
          <div className="spinner" />
          <p className="muted" style={{ marginTop: 12 }}>Connecting...</p>
        </div>
      </div>
    );
  }

  if (authState === "unauthenticated") {
    return (
      <Suspense fallback={<PageSpinner />}>
        {authView === "signup" ? <SignupPage /> : <LoginPage />}
      </Suspense>
    );
  }

  return <>{children}</>;
}

function TabSync() {
  const location = useLocation();
  const setTab = useUiStore((s) => s.setTab);

  useEffect(() => {
    const path = location.pathname.replace(/^\/+/, "");
    setTab(path || "chat");
  }, [location.pathname, setTab]);

  return null;
}

export default function App() {
  useGateway();
  useTheme();

  return (
    <ErrorBoundary>
      <AuthGate>
        <TabSync />
        <Routes>
          <Route element={<Layout />}>
            <Route index element={<Suspense fallback={<PageSpinner />}><ChatPage /></Suspense>} />
            <Route path="chat" element={<Suspense fallback={<PageSpinner />}><ChatPage /></Suspense>} />
            <Route path="overview" element={<Suspense fallback={<PageSpinner />}><OverviewPage /></Suspense>} />
            <Route path="channels" element={<Suspense fallback={<PageSpinner />}><ChannelsPage /></Suspense>} />
            <Route path="instances" element={<Suspense fallback={<PageSpinner />}><InstancesPage /></Suspense>} />
            <Route path="sessions" element={<Suspense fallback={<PageSpinner />}><SessionsPage /></Suspense>} />
            <Route path="usage" element={<Suspense fallback={<PageSpinner />}><UsagePage /></Suspense>} />
            <Route path="cron" element={<Suspense fallback={<PageSpinner />}><CronPage /></Suspense>} />
            <Route path="agents" element={<Suspense fallback={<PageSpinner />}><AgentsPage /></Suspense>} />
            <Route path="skills" element={<Suspense fallback={<PageSpinner />}><SkillsPage /></Suspense>} />
            <Route path="nodes" element={<Suspense fallback={<PageSpinner />}><NodesPage /></Suspense>} />
            <Route path="config" element={<Suspense fallback={<PageSpinner />}><ConfigPage /></Suspense>} />
            <Route path="debug" element={<Suspense fallback={<PageSpinner />}><DebugPage /></Suspense>} />
            <Route path="logs" element={<Suspense fallback={<PageSpinner />}><LogsPage /></Suspense>} />
            <Route path="org" element={<Suspense fallback={<PageSpinner />}><OrgPage /></Suspense>} />
            <Route path="org/:agentId/chat" element={<Suspense fallback={<PageSpinner />}><OrgChatPage /></Suspense>} />
            <Route path="*" element={<Navigate to="/chat" replace />} />
          </Route>
        </Routes>
      </AuthGate>
    </ErrorBoundary>
  );
}
