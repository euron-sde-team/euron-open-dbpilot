"use client";

import {
  CheckCircle2Icon,
  DatabaseIcon,
  MoreHorizontalIcon,
  PlugZapIcon,
  PlusIcon,
  ShieldCheckIcon,
  TableIcon,
  Trash2Icon,
  XCircleIcon,
} from "lucide-react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import * as React from "react";
import { toast } from "sonner";

import { ConfirmDialog } from "@/components/confirm-dialog";
import { EmptyState } from "@/components/empty-state";
import { ErrorState } from "@/components/error-state";
import { Hint } from "@/components/hint";
import { PageHeader } from "@/components/page-header";
import { RoleNote } from "@/components/role-note";
import { KindChip } from "@/components/status-chips";
import { Button } from "@/components/ui/button";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from "@/components/ui/dropdown-menu";
import { Skeleton } from "@/components/ui/skeleton";
import { useCurrentProject } from "@/features/projects/api";
import { errorMessage } from "@/lib/api/errors";
import type { ConnectionOut } from "@/lib/api/types";
import { formatBytes, formatNumber, formatRelative } from "@/lib/format";
import { kindLabel } from "@/lib/status";

import { useConnections, useDeleteConnection, useTestConnection } from "../api";

import { ConnectionDialog } from "./connection-dialog";

export function ConnectionsScreen() {
  const { pid, canManageConnections } = useCurrentProject();
  const q = useConnections(pid);
  const params = useSearchParams();
  const router = useRouter();
  const [adding, setAdding] = React.useState(false);

  React.useEffect(() => {
    if (params.get("new") === "1") setAdding(true);
  }, [params]);

  function setDialog(next: boolean) {
    setAdding(next);
    if (!next && params.get("new")) router.replace(`/projects/${pid}/connections`, { scroll: false });
  }

  const list = q.data ?? [];
  return (
    <>
      <PageHeader
        title="Connections"
        description="The databases this project shares. Every connection is opened read-only wherever the engine supports it, and every query runs under its limits."
        actions={
          canManageConnections && (
            <Button onClick={() => setDialog(true)}>
              <PlusIcon />
              Add connection
            </Button>
          )
        }
      />
      {!canManageConnections && <RoleNote className="mb-4">You can browse connections you are granted and run read-only queries. Project admins and owners add and change connections.</RoleNote>}
      {q.isPending ? (
        <div className="grid gap-4 lg:grid-cols-2">
          {[0, 1].map((i) => (
            <Skeleton key={i} className="h-56 rounded-[14px]" />
          ))}
        </div>
      ) : q.isError ? (
        <div className="surface">
          <ErrorState error={q.error} title="Connections could not be loaded" onRetry={() => void q.refetch()} />
        </div>
      ) : !list.length ? (
        <div className="surface">
          <EmptyState
            icon={DatabaseIcon}
            title="No connections yet"
            description="Connect PostgreSQL, MySQL, SQLite, SQL Server, MongoDB or Redis. DBPilot reads its schema, and every teammate you grant can browse and query it without ever seeing the password."
            action={
              canManageConnections ? (
                <Button onClick={() => setDialog(true)}>
                  <PlusIcon />
                  Add connection
                </Button>
              ) : undefined
            }
          />
        </div>
      ) : (
        <div className="grid gap-4 lg:grid-cols-2">
          {list.map((c) => (
            <ConnectionCard key={c.id} pid={pid} c={c} canManage={canManageConnections} />
          ))}
        </div>
      )}
      <ConnectionDialog pid={pid} open={adding} onOpenChange={setDialog} />
    </>
  );
}

function ConnectionCard({ pid, c, canManage }: { pid: string; c: ConnectionOut; canManage: boolean }) {
  const test = useTestConnection(pid, c.id);
  const del = useDeleteConnection(pid, c.id);
  const [deleting, setDeleting] = React.useState(false);
  const where = c.kind === "sqlite" ? `${c.file_name ?? "file"} (${formatBytes(c.file_size)})` : `${c.username || "?"}@${c.host}:${c.port}/${c.database || ""}`;
  return (
    <article className="surface flex min-w-0 flex-col gap-4 p-5" data-qa="connection">
      <div className="flex items-start gap-3">
        <span className="flex size-10 shrink-0 items-center justify-center rounded-xl bg-primary-soft text-primary">
          <DatabaseIcon className="size-5" />
        </span>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <h2 className="truncate text-[15px] font-semibold tracking-[-0.01em]">{c.name}</h2>
            <KindChip kind={c.kind} />
          </div>
          <p className="mt-0.5 truncate font-mono text-[11.5px] text-subtle" title={where}>
            {where}
          </p>
          {c.description && <p className="mt-1 line-clamp-2 text-[12.5px] text-muted-foreground">{c.description}</p>}
        </div>
        {canManage && (
          <DropdownMenu>
            <DropdownMenuTrigger render={<Button variant="subtle" size="icon-sm" aria-label={`More for ${c.name}`} />}>
              <MoreHorizontalIcon />
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end">
              <DropdownMenuItem variant="destructive" onClick={() => setDeleting(true)}>
                <Trash2Icon />
                Delete connection
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        )}
      </div>

      <div className="grid grid-cols-3 gap-2 rounded-xl bg-muted/70 px-4 py-3 text-[12px]">
        <Figure label="Objects" value={c.schema_objects != null ? String(c.schema_objects) : "Not read yet"} />
        <Figure label="Max rows" value={formatNumber(c.max_rows)} hint="maxRows" />
        <Figure label="Timeout" value={`${c.timeout_s} s`} hint="timeout" />
      </div>

      <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5 text-[12px]">
        {c.access.can_write ? (
          <span className="inline-flex items-center gap-1 font-medium text-warning">You may write here</span>
        ) : (
          <span className="inline-flex items-center gap-1 font-medium text-savings">
            <ShieldCheckIcon className="size-3.5" aria-hidden />
            Read-only
            <Hint term="readOnlySession" />
          </span>
        )}
        {c.last_tested_at ? (
          c.last_test_ok ? (
            <span className="inline-flex items-center gap-1 text-muted-foreground">
              <CheckCircle2Icon className="size-3.5 text-savings" aria-hidden />
              Tested {formatRelative(c.last_tested_at)}
            </span>
          ) : (
            <span className="inline-flex items-center gap-1 text-danger" title={c.last_test_message ?? ""}>
              <XCircleIcon className="size-3.5" aria-hidden />
              Last test failed
            </span>
          )
        ) : (
          <span className="text-subtle">Not tested yet</span>
        )}
        {c.server_version && <span className="truncate text-subtle">{c.server_version.split(" on ")[0]?.slice(0, 40)}</span>}
      </div>

      <div className="mt-auto flex flex-wrap gap-2">
        <Button variant="outline" size="sm" nativeButton={false} render={<Link href={`/projects/${pid}/connections/${c.id}`} />}>
          <TableIcon />
          Schema and health
        </Button>
        {canManage && (
          <Button
            variant="ghost"
            size="sm"
            disabled={test.isPending}
            onClick={() =>
              test.mutate(undefined, {
                onSuccess: (r) => (r.ok ? toast.success(`${r.message} (${Math.round(r.latency_ms)} ms)`) : toast.error(r.message)),
                onError: (err) => toast.error(errorMessage(err)),
              })
            }
          >
            <PlugZapIcon />
            {test.isPending ? "Testing..." : "Test"}
          </Button>
        )}
      </div>
      <ConfirmDialog
        open={deleting}
        onOpenChange={setDeleting}
        title={`Delete ${c.name}?`}
        description={`Its schema cache, saved queries and grants go with it. The ${kindLabel(c.kind)} database itself is untouched, and the audit log keeps every query that ran against it.`}
        confirmLabel="Delete connection"
        pending={del.isPending}
        onConfirm={() =>
          del.mutate(undefined, {
            onSuccess: () => {
              setDeleting(false);
              toast.success(`${c.name} deleted.`);
            },
            onError: (err) => toast.error(errorMessage(err)),
          })
        }
      />
    </article>
  );
}

function Figure({ label, value, hint }: { label: string; value: string; hint?: "rowLimit" | "timeout" | "maxRows" | "scanLimit" }) {
  return (
    <div className="min-w-0">
      <div className="flex items-center gap-1 text-subtle">
        {label}
        {hint && <Hint term={hint} />}
      </div>
      <div className="mt-0.5 truncate text-[14px] font-semibold tabular">{value}</div>
    </div>
  );
}
