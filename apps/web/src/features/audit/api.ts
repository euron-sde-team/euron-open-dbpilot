"use client";

import { useInfiniteQuery } from "@tanstack/react-query";

import { api, unwrap } from "@/lib/api/client";
import { qk, STALE } from "@/lib/query-keys";

export function useAudit(pid: string, action?: string) {
  return useInfiniteQuery({
    queryKey: qk.audit(pid, { action }),
    initialPageParam: null as string | null,
    queryFn: ({ pageParam }) =>
      unwrap(
        api.GET("/api/v1/projects/{project_id}/audit", {
          params: { path: { project_id: pid }, query: { cursor: pageParam, limit: 50 } },
        }),
      ),
    getNextPageParam: (last) => last.next_cursor ?? null,
    staleTime: STALE.config,
    enabled: !!pid,
  });
}
