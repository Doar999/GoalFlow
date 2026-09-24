import type { ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link, useOutletContext, useParams } from "react-router";

import type { AuthSession } from "@/features/auth/api";
import { getGoal, getProfileDraft } from "@/features/goals/api";
import { toApiFailure } from "@/shared/api/errors";

const STATUS_LABELS = {
  draft: "草稿",
  active: "执行中",
  paused: "已暂停",
  completed: "已完成",
  stopped: "已终止",
} as const;

export function GoalPage(): ReactNode {
  const { goalId } = useParams();
  const session = useOutletContext<AuthSession>();
  const goal = useQuery({
    queryKey: ["goals", session.user.id, goalId],
    queryFn: () => getGoal(goalId ?? ""),
    enabled: goalId !== undefined,
  });
  const draft = useQuery({
    queryKey: ["profile-draft", session.user.id, goalId],
    queryFn: () => getProfileDraft(goalId ?? ""),
    enabled: goalId !== undefined && goal.data?.status === "draft",
  });

  if (goal.isPending) return <p role="status">正在读取目标…</p>;
  if (goal.error !== null) return <p role="alert">{toApiFailure(goal.error).message}</p>;

  const description = draft.data?.content["initial_description"];
  return (
    <div className="narrow-page">
      <div className="page-kicker">目标档案</div>
      <div className="page-heading-row">
        <h1>{goal.data.title}</h1>
        <span className="status-pill">{STATUS_LABELS[goal.data.status]}</span>
      </div>
      <p className="page-lead">目标已保存。当前状态以服务端记录为准。</p>
      {goal.data.status === "draft" ? (
        <>
          <section className="panel">
            <h2>你的原始描述</h2>
            {draft.isPending && <p role="status">正在读取档案草稿…</p>}
            {draft.error !== null && <p role="alert">{toApiFailure(draft.error).message}</p>}
            {typeof description === "string" ? (
              <p className="goal-description">{description}</p>
            ) : (
              <p className="muted">暂无原始描述。</p>
            )}
          </section>
          <div className="notice planning-notice">
            <strong>规划流程正在接入</strong>
            <p>目标澄清和方案生成尚未接入模型作业。目标草稿已保存，但不会出现在今日待办中。</p>
          </div>
        </>
      ) : (
        <div className="notice">此目标的计划展示正在接入。当前目标状态以服务端记录为准。</div>
      )}
      <div className="page-links">
        <Link to="/today">返回今日</Link>
        <Link to="/goals/new">创建另一个目标</Link>
      </div>
    </div>
  );
}
