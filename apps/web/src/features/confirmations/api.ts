"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api, unwrap } from "@/lib/api/client";
import { qk, STALE } from "@/lib/query-keys";

const p = (pid: string) => ({ params: { path: { project_id: pid } } });
const cf = (pid: string, id: string) => ({ params: { path: { project_id: pid, confirmation_id: id } } });

/**
 * A human-marked write: describes exactly what will run and returns a pending
 * confirmation. Nothing executes here (AC5).
 */
export function useCreateWrite(pid: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body: { connection_id: string; sql: string; note?: string }) => unwrap(api.POST("/api/v1/projects/{project_id}/writes", { ...p(pid), body })),
    onSuccess: () => client.invalidateQueries({ queryKey: qk.confirmations(pid) }),
  });
}

export type ConfirmationStatusFilter = "pending" | "executing" | "executed" | "failed" | "cancelled" | "expired";

export function useConfirmations(pid: string, status?: ConfirmationStatusFilter) {
  return useQuery({
    queryKey: qk.confirmations(pid, status),
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/projects/{project_id}/confirmations", {
          params: { path: { project_id: pid }, query: status ? { status } : {} },
        }),
      ),
    staleTime: STALE.live,
    enabled: !!pid,
  });
}

export function useConfirmation(pid: string, id: string, enabled = true) {
  return useQuery({
    queryKey: qk.confirmation(pid, id),
    queryFn: () => unwrap(api.GET("/api/v1/projects/{project_id}/confirmations/{confirmation_id}", cf(pid, id))),
    staleTime: STALE.live,
    enabled: enabled && !!pid && !!id,
  });
}

/**
 * The second, separate confirmation step. The server requires BOTH: `confirm_token`
 * (the one-time token issued alongside the pending confirmation, proving this is the
 * same browser session that saw the preview) and `digest` (the checksum of exactly
 * what will run, proving nothing changed since it was shown). Sending the digest as
 * the token is rejected server side with `token_mismatch`, confirmed live.
 */
export function useConfirm(pid: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ id, confirmToken, digest }: { id: string; confirmToken: string; digest: string }) =>
      unwrap(api.POST("/api/v1/projects/{project_id}/confirmations/{confirmation_id}/confirm", { ...cf(pid, id), body: { confirm_token: confirmToken, digest } })),
    onSuccess: (_data, vars) => {
      client.invalidateQueries({ queryKey: qk.confirmations(pid) });
      client.invalidateQueries({ queryKey: qk.confirmation(pid, vars.id) });
      client.invalidateQueries({ queryKey: qk.audit(pid) });
    },
  });
}

export function useCancelConfirmation(pid: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => unwrap(api.POST("/api/v1/projects/{project_id}/confirmations/{confirmation_id}/cancel", cf(pid, id))),
    onSuccess: (_data, id) => {
      client.invalidateQueries({ queryKey: qk.confirmations(pid) });
      client.invalidateQueries({ queryKey: qk.confirmation(pid, id) });
    },
  });
}
