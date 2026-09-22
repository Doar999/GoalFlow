import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";

export function createQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        // 401 之后必须清理当前用户缓存，不能短暂显示上一位用户的内容。
        // 具体清理时机由 T10 的会话逻辑接管，这里只保证默认不长期复用。
        staleTime: 0,
        retry: false,
      },
    },
  });
}

export function AppProviders({
  children,
  client,
}: {
  children: ReactNode;
  client?: QueryClient;
}): ReactNode {
  return (
    <QueryClientProvider client={client ?? createQueryClient()}>{children}</QueryClientProvider>
  );
}
