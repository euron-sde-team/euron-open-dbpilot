"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api, unwrap } from "@/lib/api/client";
import { qk, STALE } from "@/lib/query-keys";

const p = (pid: string) => ({ params: { path: { project_id: pid } } });
const cp = (pid: string, id: string) => ({ params: { path: { project_id: pid, comparison_id: id } } });

export function useComparisons(pid: string) {
  return useQuery({
    queryKey: qk.comparisons(pid),
    queryFn: () => unwrap(api.GET("/api/v1/projects/{project_id}/comparisons", p(pid))),
    staleTime: STALE.list,
    enabled: !!pid,
  });
}

export function useComparison(pid: string, id: string, enabled = true) {
  return useQuery({
    queryKey: qk.comparison(pid, id),
    queryFn: () => unwrap(api.GET("/api/v1/projects/{project_id}/comparisons/{comparison_id}", cp(pid, id))),
    staleTime: STALE.detail,
    enabled: enabled && !!pid && !!id,
  });
}

export function useCreateComparison(pid: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body: { from_connection_id: string; to_connection_id: string }) => unwrap(api.POST("/api/v1/projects/{project_id}/comparisons", { ...p(pid), body })),
    onSuccess: () => client.invalidateQueries({ queryKey: qk.comparisons(pid) }),
  });
}

/**
 * Describes the migration and returns a pending confirmation (AC8). DBPilot never runs
 * the generated script itself: this only prepares the same confirmed-write path as AC5.
 */
export function useRunComparison(pid: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => unwrap(api.POST("/api/v1/projects/{project_id}/comparisons/{comparison_id}/run", cp(pid, id))),
    onSuccess: () => client.invalidateQueries({ queryKey: qk.confirmations(pid) }),
  });
}
