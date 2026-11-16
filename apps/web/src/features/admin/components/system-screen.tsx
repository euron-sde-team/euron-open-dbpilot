"use client";

import { ActivityIcon, CpuIcon, DatabaseIcon, HardDriveIcon, KeyIcon } from "lucide-react";

import { AdminOnly } from "@/components/admin-only";
import { ErrorState } from "@/components/error-state";
import { PageHeader } from "@/components/page-header";
import { Stat } from "@/components/stat";
import { Surface, SurfaceHeader } from "@/components/surface";
import { Chip } from "@/components/ui/chip";
import { Skeleton } from "@/components/ui/skeleton";
import { usePermissions } from "@/features/auth/api";
import { formatRelative } from "@/lib/format";

import { useSystem } from "../api";

export function SystemScreen() {
  const { instanceAdmin } = usePermissions();
  const q = useSystem();

  if (!instanceAdmin) return <AdminOnly what="Instance status" who="instance administrators" />;

  if (q.isPending) {
    return (
      <div className="space-y-4">
        <Skeleton className="h-10 w-72" />
        <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
          {[0, 1, 2, 3].map((i) => (
            <Skeleton key={i} className="h-24 rounded-[14px]" />
          ))}
        </div>
      </div>
    );
  }
  if (q.isError) {
    return (
      <div className="surface">
        <ErrorState error={q.error} title="System status could not be loaded" onRetry={() => void q.refetch()} />
      </div>
    );
  }

  const s = q.data;
  return (
    <>
      <PageHeader title="System" description={`DBPilot ${s.version}, running in ${s.environment}.`} />
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Stat label="Storage" icon={HardDriveIcon} value={s.storage} />
        <Stat label="Key mode" icon={KeyIcon} value={s.key_mode} />
        <Stat label="Cache" icon={ActivityIcon} value={s.redis ? `${s.cache} (redis)` : s.cache} />
        <Stat label="AI provider" icon={CpuIcon} value={s.ai_provider} />
      </div>

      <Surface className="mt-4">
        <SurfaceHeader title="Counts" />
        <div className="grid grid-cols-2 gap-3 px-5 pb-5 sm:grid-cols-4">
          {Object.entries(s.counts).map(([k, v]) => (
            <div key={k} className="min-w-0">
              <div className="truncate text-[10.5px] font-semibold tracking-[0.04em] text-subtle uppercase">{k.replace(/_/g, " ")}</div>
              <div className="tabular text-[18px] font-semibold">{v}</div>
            </div>
          ))}
        </div>
      </Surface>

      <Surface className="mt-4">
        <SurfaceHeader title="Workers" />
        {s.workers.length === 0 ? (
          <p className="px-5 pb-5 text-[13px] text-muted-foreground">No background workers reporting in. Scheduled backups will not run.</p>
        ) : (
          <ul className="divide-y divide-hairline px-5 pb-2">
            {s.workers.map((w) => (
              <li key={w.name} className="flex items-center gap-3 py-2.5">
                <span className="flex size-8 shrink-0 items-center justify-center rounded-lg bg-primary-soft text-primary">
                  <DatabaseIcon className="size-4" />
                </span>
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-[13px] font-medium">{w.name}</span>
                  <span className="block truncate text-[12px] text-subtle">v{w.version}, last seen {formatRelative(w.last_seen_at)}</span>
                </span>
                <Chip tone={w.alive ? "success" : "danger"}>{w.alive ? "Alive" : "Not responding"}</Chip>
              </li>
            ))}
          </ul>
        )}
      </Surface>
    </>
  );
}
