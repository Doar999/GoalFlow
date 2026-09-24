import { useEffect, useState } from "react";
import type { ReactNode } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  BrowserRouter,
  Link,
  Navigate,
  NavLink,
  Outlet,
  Route,
  Routes,
  useLocation,
  useNavigate,
} from "react-router";

import { AuthPage } from "@/features/auth/AuthPage";
import { getSession, logout, SESSION_QUERY_KEY } from "@/features/auth/api";
import { CreateGoalPage } from "@/features/goals/CreateGoalPage";
import { GoalPage } from "@/features/goals/GoalPage";
import { TodayPage } from "@/features/today/TodayPage";
import { toApiFailure } from "@/shared/api/errors";

function ProtectedLayout({ sessionExpired }: { sessionExpired: boolean }): ReactNode {
  const location = useLocation();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [logoutError, setLogoutError] = useState("");
  const session = useQuery({ queryKey: SESSION_QUERY_KEY, queryFn: getSession, retry: false });

  if (sessionExpired) {
    const next = encodeURIComponent(`${location.pathname}${location.search}`);
    return <Navigate to={`/login?next=${next}`} replace />;
  }

  if (session.isPending) {
    return (
      <main className="screen-message" role="status">
        正在确认登录状态…
      </main>
    );
  }
  if (session.error !== null) {
    if (toApiFailure(session.error).code === "UNAUTHENTICATED") {
      const next = encodeURIComponent(`${location.pathname}${location.search}`);
      return <Navigate to={`/login?next=${next}`} replace />;
    }
    return (
      <main className="screen-message" role="alert">
        <p>{toApiFailure(session.error).message}</p>
        <button onClick={() => void session.refetch()}>重试</button>
      </main>
    );
  }

  const currentSession = session.data;
  async function handleLogout(): Promise<void> {
    setLogoutError("");
    try {
      await logout();
      queryClient.clear();
      navigate("/login", { replace: true });
    } catch (error) {
      setLogoutError(toApiFailure(error).message);
    }
  }

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <Link to="/today" className="brand" aria-label="GoalFlow 今日页">
          <span className="brand-mark">G</span>
          <span>GoalFlow</span>
        </Link>
        <div className="sidebar-section-label">工作台</div>
        <nav aria-label="主导航" className="nav-list">
          <NavLink
            to="/today"
            className={({ isActive }) => (isActive ? "nav-link active" : "nav-link")}
          >
            今日安排
          </NavLink>
          <NavLink
            to="/goals/new"
            className={({ isActive }) => (isActive ? "nav-link active" : "nav-link")}
          >
            创建目标
          </NavLink>
        </nav>
        <div className="sidebar-bottom">
          <div className="account-label">
            当前账号<span>{currentSession.user.account_identifier}</span>
          </div>
          <button className="text-button" onClick={() => void handleLogout()}>
            退出登录
          </button>
          {logoutError !== "" && (
            <p role="alert" className="inline-error">
              {logoutError}
            </p>
          )}
        </div>
      </aside>
      <div className="content-shell">
        <header className="mobile-header">
          <Link to="/today" className="brand">
            <span className="brand-mark">G</span>GoalFlow
          </Link>
          <button className="text-button" onClick={() => void handleLogout()}>
            退出登录
          </button>
        </header>
        <main className="page-content">
          <Outlet context={currentSession} />
        </main>
        <nav className="mobile-nav" aria-label="手机导航">
          <Link to="/today">今日</Link>
          <Link to="/goals/new">新目标</Link>
        </nav>
      </div>
    </div>
  );
}

function AppRoutes(): ReactNode {
  const queryClient = useQueryClient();
  const [sessionExpired, setSessionExpired] = useState(false);
  useEffect(() => {
    const clearPrivateData = () => {
      queryClient.clear();
      setSessionExpired(true);
    };
    const restoreSession = () => setSessionExpired(false);
    window.addEventListener("goalflow:session-expired", clearPrivateData);
    window.addEventListener("goalflow:session-authenticated", restoreSession);
    return () => {
      window.removeEventListener("goalflow:session-expired", clearPrivateData);
      window.removeEventListener("goalflow:session-authenticated", restoreSession);
    };
  }, [queryClient]);

  return (
    <Routes>
      <Route path="/login" element={<AuthPage mode="login" />} />
      <Route path="/register" element={<AuthPage mode="register" />} />
      <Route element={<ProtectedLayout sessionExpired={sessionExpired} />}>
        <Route path="/today" element={<TodayPage />} />
        <Route path="/goals/new" element={<CreateGoalPage />} />
        <Route path="/goals/:goalId" element={<GoalPage />} />
      </Route>
      <Route path="*" element={<Navigate to="/today" replace />} />
    </Routes>
  );
}

export function App(): ReactNode {
  return (
    <BrowserRouter>
      <AppRoutes />
    </BrowserRouter>
  );
}
