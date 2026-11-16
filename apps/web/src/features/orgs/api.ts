"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api, unwrap } from "@/lib/api/client";
import { qk, STALE } from "@/lib/query-keys";

const o = (oid: string) => ({ params: { path: { org_id: oid } } });

/** Organizations the signed-in person belongs to, with their role in each. */
export function useOrgs() {
  return useQuery({ queryKey: qk.orgs, queryFn: () => unwrap(api.GET("/api/v1/orgs")), staleTime: STALE.list });
}

export function useOrg(oid: string | undefined) {
  return useQuery({
    queryKey: qk.org(oid ?? ""),
    queryFn: () => unwrap(api.GET("/api/v1/orgs/{org_id}", o(oid as string))),
    staleTime: STALE.config,
    enabled: !!oid,
  });
}

export function useCreateOrg() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body: { name: string }) => unwrap(api.POST("/api/v1/orgs", { body })),
    onSuccess: () => client.invalidateQueries({ queryKey: qk.orgs }),
  });
}

/** Org-wide health card: aggregates across every project the person can see. */
export function useOrgDashboard(oid: string | undefined) {
  return useQuery({
    queryKey: qk.orgDashboard(oid ?? ""),
    queryFn: () => unwrap(api.GET("/api/v1/orgs/{org_id}/dashboard", o(oid as string))),
    staleTime: STALE.live,
    enabled: !!oid,
  });
}
