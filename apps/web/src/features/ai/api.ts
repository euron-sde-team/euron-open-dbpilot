"use client";

import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api, unwrap } from "@/lib/api/client";
import { qk, STALE } from "@/lib/query-keys";

const p = (pid: string) => ({ params: { path: { project_id: pid } } });

/**
 * Natural-language-to-SQL. AC4: whatever comes back, only `executable: true` (one
 * read-only query) may ever be run; the UI must never offer to run anything else.
 */
export function useGenerateSql(pid: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body: { connection_id: string; question: string }) => unwrap(api.POST("/api/v1/projects/{project_id}/ai/sql", { ...p(pid), body })),
    onSuccess: () => client.invalidateQueries({ queryKey: qk.generations(pid) }),
  });
}

/** Run an accepted AI generation through the normal read path (still guarded server side). */
export function useRunGeneration(pid: string) {
  return useMutation({
    mutationFn: ({ generationId, pageSize }: { generationId: string; pageSize?: number }) =>
      unwrap(
        api.POST("/api/v1/projects/{project_id}/ai/generations/{generation_id}/run", {
          params: { path: { project_id: pid, generation_id: generationId } },
          body: { page_size: pageSize ?? 100 },
        }),
      ),
  });
}

export function useGenerations(pid: string) {
  return useInfiniteQuery({
    queryKey: qk.generations(pid),
    initialPageParam: null as string | null,
    queryFn: ({ pageParam }) =>
      unwrap(
        api.GET("/api/v1/projects/{project_id}/ai/generations", {
          params: { path: { project_id: pid }, query: { cursor: pageParam, limit: 50 } },
        }),
      ),
    getNextPageParam: (last) => last.next_cursor ?? null,
    staleTime: STALE.list,
    enabled: !!pid,
  });
}

export function useGeneration(pid: string, gid: string, enabled = true) {
  return useQuery({
    queryKey: qk.generation(pid, gid),
    queryFn: () => unwrap(api.GET("/api/v1/projects/{project_id}/ai/generations/{generation_id}", { params: { path: { project_id: pid, generation_id: gid } } })),
    staleTime: STALE.detail,
    enabled: enabled && !!pid && !!gid,
  });
}

export function useExplainSql(pid: string) {
  return useMutation({
    mutationFn: (body: { connection_id: string; sql: string }) => unwrap(api.POST("/api/v1/projects/{project_id}/ai/explain", { ...p(pid), body })),
  });
}

export function useDiagnose(pid: string) {
  return useMutation({
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    mutationFn: (body: any) => unwrap(api.POST("/api/v1/projects/{project_id}/ai/diagnose", { ...p(pid), body })),
  });
}

export function useIndexSuggestions(pid: string) {
  return useMutation({
    mutationFn: (body: { connection_id: string }) => unwrap(api.POST("/api/v1/projects/{project_id}/ai/index-suggestions", { ...p(pid), body })),
  });
}
