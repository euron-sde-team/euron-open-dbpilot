"use client";

import { useQuery } from "@tanstack/react-query";

import { api, unwrap } from "@/lib/api/client";
import { STALE } from "@/lib/query-keys";

/** Instance-wide status: version, storage/key/cache mode, AI provider, workers, counts. */
export function useSystem() {
  return useQuery({
    queryKey: ["system"] as const,
    queryFn: () => unwrap(api.GET("/api/v1/system")),
    staleTime: STALE.config,
  });
}
