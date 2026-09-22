import { useQuery } from "@tanstack/react-query";
import type { ReactNode } from "react";

import { toApiFailure } from "@/shared/api/errors";
import { fetchHealth } from "@/shared/api/health";

/**
 * 工程自检页。
 *
 * **这不是产品界面。** 它存在的唯一目的是让 T02 的链路可被肉眼验证：
 * 生成类型 → openapi-fetch 客户端 → TanStack Query → 统一错误映射。
 * 真正的页面与路由属于 T10，届时本组件会被替换掉。
 */
export function App(): ReactNode {
  const { data, error, isPending } = useQuery({
    queryKey: ["meta", "health"],
    queryFn: fetchHealth,
  });

  return (
    <main>
      <h1>GoalFlow</h1>
      <p>工程自检页（样例，非产品界面）</p>

      {isPending && <p role="status">正在检查后端……</p>}

      {error !== null && !isPending && (
        <p role="alert">后端不可用：{toApiFailure(error).message}</p>
      )}

      {data !== undefined && <p role="status">后端状态：{data.status}</p>}
    </main>
  );
}
