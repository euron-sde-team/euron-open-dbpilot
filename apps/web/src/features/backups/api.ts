"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api, unwrap } from "@/lib/api/client";
import { qk, STALE } from "@/lib/query-keys";

const p = (pid: string) => ({ params: { path: { project_id: pid } } });
const b = (pid: string, id: string) => ({ params: { path: { project_id: pid, backup_id: id } } });

export function useBackups(pid: string, cid?: string) {
  return useQuery({
    queryKey: qk.backups(pid, cid),
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/projects/{project_id}/backups", {
          params: { path: { project_id: pid }, query: cid ? { connection_id: cid } : {} },
        }),
      ),
    staleTime: STALE.list,
    enabled: !!pid,
  });
}

export function useBackup(pid: string, id: string, enabled = true) {
  return useQuery({
    queryKey: qk.backup(pid, id),
    queryFn: () => unwrap(api.GET("/api/v1/projects/{project_id}/backups/{backup_id}", b(pid, id))),
    staleTime: STALE.live,
    enabled: enabled && !!pid && !!id,
  });
}

export function useCreateBackup(pid: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body: { connection_id: string }) => unwrap(api.POST("/api/v1/projects/{project_id}/backups", { ...p(pid), body })),
    onSuccess: () => client.invalidateQueries({ queryKey: qk.backups(pid) }),
  });
}

export function useDeleteBackup(pid: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => unwrap(api.DELETE("/api/v1/projects/{project_id}/backups/{backup_id}", b(pid, id))),
    onSuccess: () => client.invalidateQueries({ queryKey: qk.backups(pid) }),
  });
}

export function useVerifyBackup(pid: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => unwrap(api.POST("/api/v1/projects/{project_id}/backups/{backup_id}/verify", b(pid, id))),
    onSuccess: (_data, id) => {
      client.invalidateQueries({ queryKey: qk.backup(pid, id) });
      client.invalidateQueries({ queryKey: qk.backups(pid) });
    },
  });
}

/** Describes the restore and returns a pending confirmation; nothing restores yet (AC7). */
export function useRequestRestore(pid: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ id, targetConnectionId }: { id: string; targetConnectionId: string }) =>
      unwrap(api.POST("/api/v1/projects/{project_id}/backups/{backup_id}/restore", { ...b(pid, id), body: { target_connection_id: targetConnectionId } })),
    onSuccess: () => client.invalidateQueries({ queryKey: qk.confirmations(pid) }),
  });
}

export function downloadBackupUrl(pid: string, id: string): string {
  return `/api/v1/projects/${pid}/backups/${id}/download`;
}

export function useSchedules(pid: string) {
  return useQuery({
    queryKey: qk.schedules(pid),
    queryFn: () => unwrap(api.GET("/api/v1/projects/{project_id}/backup-schedules", p(pid))),
    staleTime: STALE.list,
    enabled: !!pid,
  });
}

export function useCreateSchedule(pid: string) {
  const client = useQueryClient();
  return useMutation({
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    mutationFn: (body: any) => unwrap(api.POST("/api/v1/projects/{project_id}/backup-schedules", { ...p(pid), body })),
    onSuccess: () => client.invalidateQueries({ queryKey: qk.schedules(pid) }),
  });
}

export function useUpdateSchedule(pid: string) {
  const client = useQueryClient();
  return useMutation({
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    mutationFn: ({ id, body }: { id: string; body: any }) =>
      unwrap(api.PATCH("/api/v1/projects/{project_id}/backup-schedules/{schedule_id}", { params: { path: { project_id: pid, schedule_id: id } }, body })),
    onSuccess: () => client.invalidateQueries({ queryKey: qk.schedules(pid) }),
  });
}

export function useDeleteSchedule(pid: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (id: string) =>
      unwrap(api.DELETE("/api/v1/projects/{project_id}/backup-schedules/{schedule_id}", { params: { path: { project_id: pid, schedule_id: id } } })),
    onSuccess: () => client.invalidateQueries({ queryKey: qk.schedules(pid) }),
  });
}
