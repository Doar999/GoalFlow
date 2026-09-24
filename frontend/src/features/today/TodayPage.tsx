import type { ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link, useOutletContext } from "react-router";

import type { AuthSession } from "@/features/auth/api";
import { getAgenda } from "@/features/today/api";
import { toApiFailure } from "@/shared/api/errors";
import { localDateInTimezone } from "@/shared/lib/localDate";

export function TodayPage(): ReactNode {
  const session = useOutletContext<AuthSession>();
  const localDate = localDateInTimezone(session.user.timezone);
  const agenda = useQuery({
    queryKey: ["agenda", session.user.id, localDate],
    queryFn: () => getAgenda(localDate),
  });
  const current = agenda.data?.current;

  return (
    <div className="dashboard-page">
      <div className="page-kicker">今日 · {localDate}</div>
      <div className="dashboard-heading">
        <div>
          <h1>今天，从清晰的一步开始。</h1>
          <p className="page-lead">查看今天的安排，或者先记录一个想推进的目标。</p>
        </div>
        <Link to="/goals/new" className="primary-button">
          创建目标
        </Link>
      </div>
      {agenda.isPending && (
        <div className="panel" role="status">
          正在读取今日安排…
        </div>
      )}
      {agenda.error !== null && (
        <div className="panel" role="alert">
          {toApiFailure(agenda.error).message}
        </div>
      )}
      {agenda.data !== undefined && current === null && (
        <section className="empty-state panel">
          <div className="empty-icon">✦</div>
          <span className="eyebrow">今天的安排</span>
          <h2>今天还没有排期结果</h2>
          <p>草稿目标不会自动进入正式待办。你可以先创建目标，随后继续完成规划。</p>
          <Link to="/goals/new" className="secondary-button">
            写下第一个目标
          </Link>
        </section>
      )}
      {current !== null && current !== undefined && (
        <div className="dashboard-grid">
          <section className="metric panel">
            <span>已安排任务</span>
            <strong>{current.items.length}</strong>
            <small>项</small>
          </section>
          <section className="metric panel">
            <span>预计投入</span>
            <strong>{current.capacity.reserved_minutes}</strong>
            <small>分钟</small>
          </section>
          <section className="metric panel">
            <span>剩余容量</span>
            <strong>{current.capacity.remaining_minutes}</strong>
            <small>分钟</small>
          </section>
          <section className="panel full-width">
            <h2>排期概况</h2>
            <p className="muted">
              当前排期为 {current.status === "conflicted" ? "有冲突" : "已生成"}
              。任务名称与执行反馈将随 T11 的任务读取接口接入。
            </p>
          </section>
        </div>
      )}
    </div>
  );
}
