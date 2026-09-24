import { useState } from "react";
import type { FormEvent, ReactNode } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate, useSearchParams } from "react-router";

import { getRegistrationStatus, login, register, SESSION_QUERY_KEY } from "@/features/auth/api";
import { toApiFailure } from "@/shared/api/errors";

function safeNext(value: string | null): string {
  return value !== null && value.startsWith("/") && !value.startsWith("//") && !value.includes("\\")
    ? value
    : "/today";
}

export function AuthPage({ mode }: { mode: "login" | "register" }): ReactNode {
  const isRegister = mode === "register";
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const queryClient = useQueryClient();
  const registration = useQuery({
    queryKey: ["auth", "registration"],
    queryFn: getRegistrationStatus,
  });
  const [accountIdentifier, setAccountIdentifier] = useState("");
  const [password, setPassword] = useState("");
  const [timezone, setTimezone] = useState(
    () => Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC",
  );
  const [errorMessage, setErrorMessage] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);
  const destination = safeNext(searchParams.get("next"));
  const nextParam = `?next=${encodeURIComponent(destination)}`;

  async function handleSubmit(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    if (isSubmitting || (isRegister && registration.data !== true)) return;
    setIsSubmitting(true);
    setErrorMessage("");
    try {
      const session = isRegister
        ? await register({ account_identifier: accountIdentifier, password, timezone })
        : await login({ account_identifier: accountIdentifier, password });
      queryClient.clear();
      queryClient.setQueryData(SESSION_QUERY_KEY, session);
      window.dispatchEvent(new Event("goalflow:session-authenticated"));
      navigate(destination, { replace: true });
    } catch (error) {
      setErrorMessage(toApiFailure(error).message);
    } finally {
      setIsSubmitting(false);
    }
  }

  return (
    <div className="auth-screen">
      <div className="auth-intro">
        <div className="brand brand-on-dark">
          <span className="brand-mark">G</span>GoalFlow
        </div>
        <div className="auth-intro-copy">
          <span className="eyebrow">把想法变成每天可执行的一步</span>
          <h1>让目标，有清晰的下一步。</h1>
          <p>从一个真实目标开始，逐步形成计划，再根据每天的进展调整。</p>
        </div>
        <p className="auth-footer">你的目标、计划和记录只属于你的账号。</p>
      </div>
      <main className="auth-main">
        <section className="auth-card" aria-labelledby="auth-title">
          <span className="eyebrow">{isRegister ? "开始使用" : "欢迎回来"}</span>
          <h2 id="auth-title">{isRegister ? "创建账号" : "登录 GoalFlow"}</h2>
          <p className="muted">
            {isRegister ? "无需邀请码。先建立个人空间。" : "继续查看你的目标与今日安排。"}
          </p>

          {registration.isPending && isRegister && <p role="status">正在检查注册状态…</p>}
          {registration.isError && isRegister && (
            <p role="alert" className="inline-error">
              注册状态暂时无法读取，请稍后重试。
            </p>
          )}
          {isRegister && registration.data === false && (
            <div role="status" className="notice">
              此实例当前不开放新账号注册。已有账号仍可登录。
            </div>
          )}

          {(!isRegister || registration.data === true) && (
            <form onSubmit={(event) => void handleSubmit(event)} className="form-stack">
              <label htmlFor="account-identifier">账号标识</label>
              <input
                id="account-identifier"
                autoComplete="username"
                value={accountIdentifier}
                onChange={(event) => setAccountIdentifier(event.target.value)}
                minLength={3}
                maxLength={254}
                required
                placeholder="用户名或邮箱格式"
              />
              <label htmlFor="password">密码</label>
              <input
                id="password"
                type="password"
                autoComplete={isRegister ? "new-password" : "current-password"}
                value={password}
                onChange={(event) => setPassword(event.target.value)}
                minLength={isRegister ? 12 : undefined}
                maxLength={128}
                required
                placeholder={isRegister ? "至少 12 个字符" : "输入密码"}
              />
              {isRegister && (
                <>
                  <label htmlFor="timezone">所在时区</label>
                  <input
                    id="timezone"
                    value={timezone}
                    onChange={(event) => setTimezone(event.target.value)}
                    required
                  />
                  <p className="field-hint">今日安排会按这个时区确定日期。</p>
                </>
              )}
              {errorMessage !== "" && (
                <p role="alert" className="inline-error">
                  {errorMessage}
                </p>
              )}
              <button className="primary-button" type="submit" disabled={isSubmitting}>
                {isSubmitting ? "正在提交…" : isRegister ? "创建账号" : "登录"}
              </button>
            </form>
          )}
          <p className="auth-switch">
            {isRegister ? "已有账号？" : "还没有账号？"}{" "}
            {isRegister ? (
              <Link to={`/login${nextParam}`}>前往登录</Link>
            ) : registration.data === true ? (
              <Link to={`/register${nextParam}`}>创建账号</Link>
            ) : (
              <span>注册状态由实例设置决定</span>
            )}
          </p>
        </section>
      </main>
    </div>
  );
}
