import { useRef, useState } from "react";
import type { FormEvent, ReactNode } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate, useOutletContext } from "react-router";

import type { AuthSession } from "@/features/auth/api";
import { createGoal } from "@/features/goals/api";
import { toApiFailure } from "@/shared/api/errors";

export function CreateGoalPage(): ReactNode {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const session = useOutletContext<AuthSession>();
  const [description, setDescription] = useState("");
  const [errorMessage, setErrorMessage] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);
  const intent = useRef<{ description: string; key: string } | null>(null);

  async function handleSubmit(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    const text = description.trim();
    if (text === "" || isSubmitting) return;
    if (intent.current?.description !== text) {
      intent.current = { description: text, key: crypto.randomUUID() };
    }
    setIsSubmitting(true);
    setErrorMessage("");
    try {
      const goal = await createGoal(text, intent.current.key);
      queryClient.setQueryData(["goals", session.user.id, goal.id], goal);
      navigate(`/goals/${goal.id}`, { replace: true });
    } catch (error) {
      setErrorMessage(toApiFailure(error).message);
    } finally {
      setIsSubmitting(false);
    }
  }

  return (
    <div className="narrow-page">
      <div className="page-kicker">新目标 · 第一步</div>
      <h1>你最近想完成什么目标？</h1>
      <p className="page-lead">
        写下想达到的结果即可。当前先保存目标草稿，后续可继续完善目标档案。
      </p>
      <form className="panel form-stack" onSubmit={(event) => void handleSubmit(event)}>
        <label htmlFor="goal-description">目标描述</label>
        <textarea
          id="goal-description"
          rows={7}
          value={description}
          onChange={(event) => setDescription(event.target.value)}
          maxLength={8000}
          required
          placeholder="例如：希望三个月后能用英语完成一次十分钟的工作介绍…"
        />
        <p className="field-hint">无需选择领域；系统会根据目标内容确定后续规划方式。</p>
        {errorMessage !== "" && (
          <p role="alert" className="inline-error">
            {errorMessage}
          </p>
        )}
        <div className="form-actions">
          <Link to="/today" className="text-button">
            返回今日
          </Link>
          <button className="primary-button" disabled={isSubmitting || description.trim() === ""}>
            {isSubmitting ? "正在保存…" : "保存目标草稿"}
          </button>
        </div>
      </form>
    </div>
  );
}
