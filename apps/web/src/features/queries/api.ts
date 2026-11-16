"use client";

import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api, unwrap } from "@/lib/api/client";
import { qk, STALE } from "@/lib/query-keys";

const p = (pid: string) => ({ params: { path: { project_id: pid } } });

export interface QueryFilters {
  connection_id?: string | null;
  status?: string | null;
  source?: string | null;
  mine?: boolean;
}

export function useQueries(pid: string, filters: QueryFilters = {}) {
  return useInfiniteQuery({
    queryKey: qk.queries(pid, filters),
    initialPageParam: null as string | null,
    queryFn: ({ pageParam }) =>
      unwrap(
        api.GET("/api/v1/projects/{project_id}/queries", {
          params: {
            path: { project_id: pid },
            // eslint-disable-next-line @typescript-eslint/no-explicit-any
            query: { ...filters, cursor: pageParam, limit: 50 } as any,
          },
        }),
      ),
    getNextPageParam: (last) => last.next_cursor ?? null,
    staleTime: STALE.list,
    enabled: !!pid,
  });
}

export function useQueryRun(pid: string, qid: string, enabled = true) {
  return useQuery({
    queryKey: qk.query(pid, qid),
    queryFn: () => unwrap(api.GET("/api/v1/projects/{project_id}/queries/{query_id}", { params: { path: { project_id: pid, query_id: qid } } })),
    staleTime: STALE.detail,
    enabled: enabled && !!pid && !!qid,
  });
}

export function useQueryRows(pid: string, qid: string, offset: number, limit: number, enabled = true) {
  return useQuery({
    queryKey: qk.queryRows(pid, qid, offset, limit),
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/projects/{project_id}/queries/{query_id}/rows", {
          params: { path: { project_id: pid, query_id: qid }, query: { offset, limit } },
        }),
      ),
    staleTime: STALE.detail,
    enabled: enabled && !!pid && !!qid,
  });
}

/** Run SQL against a SQL-family connection through the normal read path (the guard applies). */
export function useRunSql(pid: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body: { connection_id: string; sql: string; page_size?: number }) =>
      unwrap(api.POST("/api/v1/projects/{project_id}/queries/sql", { ...p(pid), body })),
    onSuccess: () => client.invalidateQueries({ queryKey: qk.queries(pid, {}) }),
  });
}

/** Run a read-only MongoDB filter/aggregate. */
export function useRunMongo(pid: string) {
  const client = useQueryClient();
  return useMutation({
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    mutationFn: (body: any) => unwrap(api.POST("/api/v1/projects/{project_id}/queries/mongodb", { ...p(pid), body })),
    onSuccess: () => client.invalidateQueries({ queryKey: qk.queries(pid, {}) }),
  });
}

/** Run a read-only Redis command. */
export function useRunRedis(pid: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body: { connection_id: string; command: string }) => unwrap(api.POST("/api/v1/projects/{project_id}/queries/redis", { ...p(pid), body })),
    onSuccess: () => client.invalidateQueries({ queryKey: qk.queries(pid, {}) }),
  });
}

export function useFormatSql(pid: string) {
  return useMutation({
    mutationFn: (body: { sql: string }) => unwrap(api.POST("/api/v1/projects/{project_id}/queries/format", { ...p(pid), body })),
  });
}

export function useValidateSql(pid: string) {
  return useMutation({
    mutationFn: (body: { sql: string }) => unwrap(api.POST("/api/v1/projects/{project_id}/queries/validate", { ...p(pid), body })),
  });
}

export function useExportUrl(pid: string, qid: string, format: "csv" | "json" = "csv"): string {
  return `/api/v1/projects/${pid}/queries/${qid}/export?format=${format}`;
}

export function useSavedQueries(pid: string, cid?: string) {
  return useQuery({
    queryKey: qk.savedQueries(pid, cid),
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/projects/{project_id}/saved-queries", {
          params: { path: { project_id: pid }, query: cid ? { connection_id: cid } : {} },
        }),
      ),
    staleTime: STALE.list,
    enabled: !!pid,
  });
}

export function useCreateSavedQuery(pid: string) {
  const client = useQueryClient();
  return useMutation({
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    mutationFn: (body: any) => unwrap(api.POST("/api/v1/projects/{project_id}/saved-queries", { ...p(pid), body })),
    onSuccess: () => client.invalidateQueries({ queryKey: qk.savedQueries(pid) }),
  });
}

export function useDeleteSavedQuery(pid: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (savedId: string) =>
      unwrap(api.DELETE("/api/v1/projects/{project_id}/saved-queries/{saved_id}", { params: { path: { project_id: pid, saved_id: savedId } } })),
    onSuccess: () => client.invalidateQueries({ queryKey: qk.savedQueries(pid) }),
  });
}
