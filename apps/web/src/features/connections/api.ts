"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api, unwrap } from "@/lib/api/client";
import type { ConnectionKind } from "@/lib/api/types";
import { qk, STALE } from "@/lib/query-keys";

const p = (pid: string) => ({ params: { path: { project_id: pid } } });
const c = (pid: string, cid: string) => ({ params: { path: { project_id: pid, connection_id: cid } } });

export function useConnections(pid: string) {
  return useQuery({
    queryKey: qk.connections(pid),
    queryFn: () => unwrap(api.GET("/api/v1/projects/{project_id}/connections", p(pid))),
    staleTime: STALE.list,
    enabled: !!pid,
  });
}

export function useConnection(pid: string, cid: string, enabled = true) {
  return useQuery({
    queryKey: qk.connection(pid, cid),
    queryFn: () => unwrap(api.GET("/api/v1/projects/{project_id}/connections/{connection_id}", c(pid, cid))),
    staleTime: STALE.detail,
    enabled: enabled && !!pid && !!cid,
  });
}

export function useConnectionHealth(pid: string, cid: string, enabled = true) {
  return useQuery({
    queryKey: qk.connectionHealth(pid, cid),
    queryFn: () => unwrap(api.GET("/api/v1/projects/{project_id}/connections/{connection_id}/health", c(pid, cid))),
    staleTime: STALE.live,
    enabled: enabled && !!pid && !!cid,
  });
}

export interface ConnectionFormValues {
  name: string;
  kind: ConnectionKind;
  description?: string;
  host: string;
  port: number | null;
  database?: string;
  username?: string;
  password?: string;
  ssl_mode?: string;
  default_schema?: string;
  auth_source?: string;
  max_rows?: number;
  max_scan_rows?: number;
  timeout_s?: number;
}

export function useCreateConnection(pid: string) {
  const client = useQueryClient();
  return useMutation({
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    mutationFn: (body: any) => unwrap(api.POST("/api/v1/projects/{project_id}/connections", { ...p(pid), body })),
    onSuccess: () => client.invalidateQueries({ queryKey: qk.connections(pid) }),
  });
}

export function useCreateSqliteConnection(pid: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (form: FormData) =>
      unwrap(
        api.POST("/api/v1/projects/{project_id}/connections/sqlite", {
          ...p(pid),
          body: form as unknown as { name: string; file: string },
          bodySerializer: (b: unknown) => b as FormData,
        }),
      ),
    onSuccess: () => client.invalidateQueries({ queryKey: qk.connections(pid) }),
  });
}

export function useUpdateConnection(pid: string, cid: string) {
  const client = useQueryClient();
  return useMutation({
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    mutationFn: (body: any) => unwrap(api.PATCH("/api/v1/projects/{project_id}/connections/{connection_id}", { ...c(pid, cid), body })),
    onSuccess: (data) => {
      client.setQueryData(qk.connection(pid, cid), data);
      void client.invalidateQueries({ queryKey: qk.connections(pid) });
    },
  });
}

export function useDeleteConnection(pid: string, cid: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: () => unwrap(api.DELETE("/api/v1/projects/{project_id}/connections/{connection_id}", c(pid, cid))),
    onSuccess: () => {
      client.removeQueries({ queryKey: qk.connection(pid, cid) });
      void client.invalidateQueries({ queryKey: qk.connections(pid) });
    },
  });
}

/** Test a connection that is already saved. */
export function useTestConnection(pid: string, cid: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: () => unwrap(api.POST("/api/v1/projects/{project_id}/connections/{connection_id}/test", c(pid, cid))),
    onSuccess: () => client.invalidateQueries({ queryKey: qk.connection(pid, cid) }),
  });
}

/** Test connection details before saving (the new-connection form). */
export function useTestDraftConnection(pid: string) {
  return useMutation({
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    mutationFn: (body: any) => unwrap(api.POST("/api/v1/projects/{project_id}/connections/test", { ...p(pid), body })),
  });
}

export function useSchema(pid: string, cid: string, enabled = true) {
  return useQuery({
    queryKey: qk.schema(pid, cid),
    queryFn: () => unwrap(api.GET("/api/v1/projects/{project_id}/connections/{connection_id}/schema", c(pid, cid))),
    staleTime: STALE.static,
    enabled: enabled && !!pid && !!cid,
  });
}

export function useRefreshSchema(pid: string, cid: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: () => unwrap(api.POST("/api/v1/projects/{project_id}/connections/{connection_id}/schema/refresh", c(pid, cid))),
    onSuccess: (data) => {
      client.setQueryData(qk.schema(pid, cid), data);
      void client.invalidateQueries({ queryKey: qk.relationships(pid, cid) });
    },
  });
}

export function useRelationships(pid: string, cid: string, enabled = true) {
  return useQuery({
    queryKey: qk.relationships(pid, cid),
    queryFn: () => unwrap(api.GET("/api/v1/projects/{project_id}/connections/{connection_id}/schema/relationships", c(pid, cid))),
    staleTime: STALE.static,
    enabled: enabled && !!pid && !!cid,
  });
}

export function useSchemaSearch(pid: string, cid: string, q: string, enabled = true) {
  return useQuery({
    queryKey: qk.schemaSearch(pid, cid, q),
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/projects/{project_id}/connections/{connection_id}/schema/search", {
          params: { path: { project_id: pid, connection_id: cid }, query: { q } },
        }),
      ),
    staleTime: STALE.detail,
    enabled: enabled && !!pid && !!cid && q.trim().length > 0,
  });
}

export function useSnapshots(pid: string, cid: string, enabled = true) {
  return useQuery({
    queryKey: qk.snapshots(pid, cid),
    queryFn: () => unwrap(api.GET("/api/v1/projects/{project_id}/connections/{connection_id}/schema/snapshots", c(pid, cid))),
    staleTime: STALE.list,
    enabled: enabled && !!pid && !!cid,
  });
}

export function useGrants(pid: string, cid: string, enabled = true) {
  return useQuery({
    queryKey: qk.grants(pid, cid),
    queryFn: () => unwrap(api.GET("/api/v1/projects/{project_id}/connections/{connection_id}/grants", c(pid, cid))),
    staleTime: STALE.list,
    enabled: enabled && !!pid && !!cid,
  });
}

/** Grant one member (or every member, user_id null) access, optionally with writes. */
export function useSetGrant(pid: string, cid: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body: { user_id: string | null; can_write: boolean }) =>
      unwrap(api.PUT("/api/v1/projects/{project_id}/connections/{connection_id}/grants", { ...c(pid, cid), body })),
    onSuccess: () => client.invalidateQueries({ queryKey: qk.grants(pid, cid) }),
  });
}

export function useRemoveGrant(pid: string, cid: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (grantId: string) =>
      unwrap(
        api.DELETE("/api/v1/projects/{project_id}/connections/{connection_id}/grants/{grant_id}", {
          params: { path: { project_id: pid, connection_id: cid, grant_id: grantId } },
        }),
      ),
    onSuccess: () => client.invalidateQueries({ queryKey: qk.grants(pid, cid) }),
  });
}
