"use client";

import {
  ArchiveIcon,
  ArrowRightIcon,
  DatabaseIcon,
  PlugIcon,
  ScrollTextIcon,
  ShieldXIcon,
  SparklesIcon,
  SquareTerminalIcon,
} from "lucide-react";
import Link from "next/link";

import { EmptyState } from "@/components/empty-state";
import { ErrorState } from "@/components/error-state";
import { Hint } from "@/components/hint";
import { PageHeader } from "@/components/page-header";
import { Stat } from "@/components/stat";
import { KindChip } from "@/components/status-chips";
import { Surface, SurfaceHeader } from "@/components/surface";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { useConnections } from "@/features/connections/api";
import { useCurrentProject } from "@/features/projects/api";
import { formatNumber, formatRelative, plural } from "@/lib/format";

import { useOverview } from "../api";

export function OverviewScreen() {
  const { pid, project, canManageConnections } = useCurrentProject();
  const q = useOverview(pid);
  const connections = useConnections(pid);
  const d = q.data?.totals;

  return (
    <>
      <PageHeader
        title={project?.name ?? "Overview"}
        description={project?.description || "What ran against this project's connections, what the guard stopped, and what is waiting on a confirmation."}
        actions={
          <Button nativeButton={false} render={<Link href={`/projects/${pid}/editor`} />}>
            <SquareTerminalIcon />
            Open SQL editor
          </Button>
        }
      />
      {q.isError ? (
        <div className="surface">
          <ErrorState error={q.error} title="The overview could not be loaded" onRetry={() => void q.refetch()} />
        </div>
      ) : (
        <>
          <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
            <Stat label="Queries, 24h" icon={SquareTerminalIcon} loading={!d} value={d ? formatNumber(d.queries_24h) : ""} detail={d ? `${formatNumber(d.queries_total)} in all` : undefined} />
            <Stat
              label="Refused by the guard, 7d"
              icon={ShieldXIcon}
              loading={!d}
              value={d ? formatNumber(d.queries_refused_7d) : ""}
              tone={d && d.queries_refused_7d > 0 ? "warn" : "default"}
              detail={<span className="inline-flex items-center gap-1">Never reached a database <Hint term="sqlGuard" /></span>}
            />
            <Stat
              label="Pending confirmations"
              icon={ScrollTextIcon}
              loading={!d}
              value={d ? formatNumber(d.pending_writes + d.pending_migrations + d.pending_restores) : ""}
              tone={d && d.pending_writes + d.pending_migrations + d.pending_restores > 0 ? "warn" : "default"}
              detail={d ? `${plural(d.pending_writes, "write")}, ${plural(d.pending_migrations, "migration")}, ${plural(d.pending_restores, "restore")}` : undefined}
            />
            <Stat
              label="Last backup"
              icon={ArchiveIcon}
              loading={!d}
              value={d ? formatRelative(d.last_backup_at) : ""}
              detail={d ? `${plural(d.backups_succeeded_7d, "succeeded")}, ${plural(d.backups_failed_7d, "failed")} in 7d` : undefined}
            />
          </div>

          <div className="mt-4 grid min-w-0 gap-4 xl:grid-cols-[1.4fr_1fr]">
            <Surface>
              <SurfaceHeader
                title="AI assistant, 7 days"
                description="Every AI-generated statement is guarded the same way; a write is never offered to run."
              />
              <div className="flex items-center gap-6 px-5 pb-5">
                <div>
                  <div className="text-[11px] font-semibold tracking-[0.04em] text-subtle uppercase">Accepted</div>
                  <div className="tabular text-[22px] font-semibold text-savings">{d ? formatNumber(d.ai_accepted_7d) : <Skeleton className="h-7 w-10" />}</div>
                </div>
                <div>
                  <div className="text-[11px] font-semibold tracking-[0.04em] text-subtle uppercase">Refused</div>
                  <div className="tabular text-[22px] font-semibold text-danger">{d ? formatNumber(d.ai_refused_7d) : <Skeleton className="h-7 w-10" />}</div>
                </div>
                <div>
                  <div className="text-[11px] font-semibold tracking-[0.04em] text-subtle uppercase">Writes executed</div>
                  <div className="tabular text-[22px] font-semibold">{d ? formatNumber(d.writes_executed_7d) : <Skeleton className="h-7 w-10" />}</div>
                </div>
                <Link href={`/projects/${pid}/editor?panel=ai`} className="ml-auto flex items-center gap-1 text-[12.5px] font-medium text-primary hover:underline">
                  <SparklesIcon className="size-3.5" />
                  Ask the assistant
                </Link>
              </div>
            </Surface>
            <Surface>
              <SurfaceHeader
                title="Connections"
                action={
                  <Link href={`/projects/${pid}/connections`} className="text-[12.5px] font-medium text-primary hover:underline">
                    All connections
                  </Link>
                }
              />
              <div className="px-3 pb-3">
                {connections.isPending ? (
                  <Skeleton className="mx-2 h-40" />
                ) : !connections.data?.length ? (
                  <EmptyState
                    compact
                    icon={DatabaseIcon}
                    title="Connect your first database"
                    description="PostgreSQL, MySQL, SQLite, SQL Server, MongoDB or Redis. The credential is sealed server side."
                    action={
                      canManageConnections ? (
                        <Button size="sm" nativeButton={false} render={<Link href={`/projects/${pid}/connections?new=1`} />}>
                          <PlugIcon />
                          Add a connection
                        </Button>
                      ) : undefined
                    }
                  />
                ) : (
                  <ul className="space-y-1">
                    {connections.data.slice(0, 6).map((c) => (
                      <li key={c.id}>
                        <Link
                          href={`/projects/${pid}/connections/${c.id}`}
                          className="flex items-center gap-3 rounded-xl px-2.5 py-2.5 transition-colors hover:bg-muted focus-visible:outline-2 focus-visible:outline-ring"
                        >
                          <span className="flex size-9 shrink-0 items-center justify-center rounded-lg bg-primary-soft text-primary">
                            <DatabaseIcon className="size-4" />
                          </span>
                          <span className="min-w-0 flex-1">
                            <span className="flex items-center gap-2">
                              <span className="truncate text-[13.5px] font-medium">{c.name}</span>
                              <KindChip kind={c.kind} />
                            </span>
                            <span className="block truncate text-[12px] text-subtle">{c.last_test_ok ? "Last test ok" : c.last_tested_at ? "Last test failed" : "Not tested yet"}</span>
                          </span>
                          <ArrowRightIcon className="size-3.5 text-faint" />
                        </Link>
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            </Surface>
          </div>
        </>
      )}
    </>
  );
}
