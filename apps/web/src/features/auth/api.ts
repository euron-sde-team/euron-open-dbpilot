"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api, unwrap } from "@/lib/api/client";
import type { MetaOut, UserOut } from "@/lib/api/types";
import { isInstanceAdmin } from "@/lib/roles";
import { qk, STALE } from "@/lib/query-keys";

/**
 * The signed-in user, or null for a signed-out visitor. The session endpoint answers a
 * signed-out visitor with 200 and no user instead of a 401 (which browsers log as an
 * error on every page view).
 */
export function useMe() {
  return useQuery({
    queryKey: qk.me,
    queryFn: async (): Promise<UserOut | null> => (await unwrap(api.GET("/api/v1/auth/session"))).user ?? null,
    staleTime: STALE.static,
    retry: false,
  });
}

/** Role flags for the current visitor. While loading, nothing privileged is shown. */
export function usePermissions(): { user: UserOut | null | undefined; signedIn: boolean; instanceAdmin: boolean } {
  const { data } = useMe();
  return { user: data, signedIn: !!data, instanceAdmin: isInstanceAdmin(data?.role) };
}

export function useLogin() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body: { email: string; password: string }) => unwrap(api.POST("/api/v1/auth/login", { body })),
    onSuccess: (session) => {
      client.clear();
      client.setQueryData(qk.me, session.user);
    },
  });
}

export function useLogout() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: () => unwrap(api.POST("/api/v1/auth/logout")),
    onSettled: () => client.clear(),
  });
}

export function useChangePassword() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body: { current_password: string; new_password: string }) => unwrap(api.POST("/api/v1/auth/password", { body })),
    onSuccess: () => client.invalidateQueries({ queryKey: qk.me }),
  });
}

/** Version and which features each connected engine supports (the real parity map, AC11). */
export function useMeta() {
  return useQuery({
    queryKey: qk.meta,
    queryFn: (): Promise<MetaOut> => unwrap(api.GET("/api/v1/meta")),
    staleTime: STALE.config,
    retry: false,
  });
}
