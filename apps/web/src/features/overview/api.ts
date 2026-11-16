"use client";

import { useQuery } from "@tanstack/react-query";

import { api, unwrap } from "@/lib/api/client";
import { qk, STALE } from "@/lib/query-keys";

/** Project totals over the connections you can see, computed live, cached 15s. */
export function useOverview(pid: string) {
  return useQuery({
    queryKey: qk.overview(pid),
    queryFn: () => unwrap(api.GET("/api/v1/projects/{project_id}/overview", { params: { path: { project_id: pid } } })),
    staleTime: STALE.live,
    enabled: !!pid,
  });
}
