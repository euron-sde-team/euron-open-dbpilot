"use client";

import { ChevronDownIcon, ChevronRightIcon, ScrollTextIcon } from "lucide-react";
import * as React from "react";

import { EmptyState } from "@/components/empty-state";
import { ErrorState } from "@/components/error-state";
import { JsonBlock } from "@/components/code-block";
import { PageHeader } from "@/components/page-header";
import { Surface } from "@/components/surface";
import { Button } from "@/components/ui/button";
import { Chip } from "@/components/ui/chip";
import { Skeleton } from "@/components/ui/skeleton";
import { useCurrentProject } from "@/features/projects/api";
import type { AuditEntryOut } from "@/lib/api/types";
import { formatDateTime, shortId } from "@/lib/format";

import { useAudit } from "../api";

const ACTION_TONE: Record<string, "success" | "warning" | "danger" | "info" | "primary" | "neutral"> = {
  "query.run": "neutral",
  "write.requested": "warning",
  "write.confirmed": "warning",
  "write.executed": "success",
  "ai.generation": "info",
  "backup.created": "primary",
  "backup.restored": "danger",
  "migration.run": "danger",
};

function toneFor(action: string): "success" | "warning" | "danger" | "info" | "primary" | "neutral" {
  return ACTION_TONE[action] ?? "neutral";
}

export function AuditScreen() {
  const { pid } = useCurrentProject();
  const q = useAudit(pid);
  const entries = q.data?.pages.flatMap((p) => p.items) ?? [];

  return (
    <>
      <PageHeader
        title="Audit log"
        description="Every query execution, AI generation, write confirmation, backup, restore and migration run, written through one shared path. There is no second way to reach a connection that skips it."
      />
      {q.isPending ? (
        <div className="space-y-2">
          {[0, 1, 2, 3].map((i) => (
            <Skeleton key={i} className="h-14 rounded-[14px]" />
          ))}
        </div>
      ) : q.isError ? (
        <div className="surface">
          <ErrorState error={q.error} title="The audit log could not be loaded" onRetry={() => void q.refetch()} />
        </div>
      ) : entries.length === 0 ? (
        <div className="surface">
          <EmptyState icon={ScrollTextIcon} title="Nothing recorded yet" description="Every action that touches a connection, a write, a backup or a migration shows up here as soon as it happens." />
        </div>
      ) : (
        <Surface>
          <ul className="divide-y divide-hairline px-2">
            {entries.map((e) => (
              <AuditRow key={e.id} e={e} />
            ))}
          </ul>
          {q.hasNextPage && (
            <div className="flex justify-center py-3">
              <Button variant="outline" size="sm" onClick={() => void q.fetchNextPage()} disabled={q.isFetchingNextPage}>
                {q.isFetchingNextPage ? "Loading..." : "Load more"}
              </Button>
            </div>
          )}
        </Surface>
      )}
    </>
  );
}

function AuditRow({ e }: { e: AuditEntryOut }) {
  const [open, setOpen] = React.useState(false);
  const hasDetails = e.details && Object.keys(e.details).length > 0;
  return (
    <li>
      <button
        type="button"
        onClick={() => hasDetails && setOpen((v) => !v)}
        aria-expanded={hasDetails ? open : undefined}
        className="flex w-full items-center gap-3 px-3 py-3 text-left hover:bg-muted/50 focus-visible:outline-2 focus-visible:outline-ring"
      >
        {hasDetails ? (
          open ? <ChevronDownIcon className="size-4 shrink-0 text-subtle" /> : <ChevronRightIcon className="size-4 shrink-0 text-subtle" />
        ) : (
          <span className="size-4 shrink-0" />
        )}
        <Chip tone={toneFor(e.action)} className="shrink-0">
          {e.action}
        </Chip>
        <span className="min-w-0 flex-1">
          <span className="block truncate text-[13px] font-medium">{e.actor_email ?? "System"}</span>
          <span className="block truncate text-[11.5px] text-subtle">
            {e.actor_role}
            {e.target_type ? `, ${e.target_type} ${shortId(e.target_id)}` : ""}
            {e.connection_id ? `, connection ${shortId(e.connection_id)}` : ""}
          </span>
        </span>
        <span className="shrink-0 text-[11.5px] text-subtle">{formatDateTime(e.at)}</span>
      </button>
      {open && hasDetails && (
        <div className="px-3 pb-3">
          <JsonBlock value={e.details} label="Details" maxLines={10} />
        </div>
      )}
    </li>
  );
}
