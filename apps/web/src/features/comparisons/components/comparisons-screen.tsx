"use client";

import { AlertTriangleIcon, GitCompareIcon, PlusIcon } from "lucide-react";
import * as React from "react";
import { toast } from "sonner";

import { EmptyState } from "@/components/empty-state";
import { ErrorState } from "@/components/error-state";
import { Hint } from "@/components/hint";
import { PageHeader } from "@/components/page-header";
import { RoleNote } from "@/components/role-note";
import { KindChip } from "@/components/status-chips";
import { Surface, SurfaceHeader } from "@/components/surface";
import { Button } from "@/components/ui/button";
import { Chip } from "@/components/ui/chip";
import { Dialog, DialogBody, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Field } from "@/components/ui/label";
import { SimpleSelect } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { useConnections } from "@/features/connections/api";
import { ConfirmWriteDialog } from "@/features/confirmations/components/confirm-write-dialog";
import { useCurrentProject } from "@/features/projects/api";
import { errorMessage } from "@/lib/api/errors";
import type { ComparisonOut, ConfirmationCreated } from "@/lib/api/types";
import { formatRelative, plural } from "@/lib/format";
import { isSqlEngine } from "@/lib/status";

import { useComparisons, useCreateComparison, useRunComparison } from "../api";

export function ComparisonsScreen() {
  const { pid, canConfirmWrites } = useCurrentProject();
  const comparisons = useComparisons(pid);
  const connections = useConnections(pid);
  const create = useCreateComparison(pid);
  const [newOpen, setNewOpen] = React.useState(false);
  const [fromId, setFromId] = React.useState("");
  const [toId, setToId] = React.useState("");
  const [confirmation, setConfirmation] = React.useState<ConfirmationCreated | null>(null);

  const sqlConnections = (connections.data ?? []).filter((c) => isSqlEngine(c.kind));
  const list = comparisons.data?.items ?? [];

  function submit() {
    if (!fromId || !toId) return;
    create.mutate(
      { from_connection_id: fromId, to_connection_id: toId },
      {
        onSuccess: () => {
          toast.success("Comparison ready.");
          setNewOpen(false);
          setFromId("");
          setToId("");
        },
        onError: (err) => toast.error(errorMessage(err)),
      },
    );
  }

  return (
    <>
      <PageHeader
        title="Schema compare"
        description={
          <span className="inline-flex items-center gap-1">
            Diff two connections&apos; schemas and generate a migration you review before anything runs <Hint term="comparison" />
          </span>
        }
        actions={
          canConfirmWrites &&
          sqlConnections.length >= 2 && (
            <Button onClick={() => setNewOpen(true)}>
              <PlusIcon />
              Compare connections
            </Button>
          )
        }
      />
      {!canConfirmWrites && <RoleNote className="mb-4">Viewers see comparisons but cannot run a generated migration. Ask a project admin.</RoleNote>}
      <div className="mb-4 rounded-xl bg-muted/70 px-4 py-2.5 text-[12.5px] text-muted-foreground">
        Schema compare works for PostgreSQL, MySQL, SQLite and SQL Server. It does not apply to MongoDB or Redis, which have no
        fixed schema to diff.
      </div>

      {comparisons.isPending ? (
        <div className="space-y-3">
          {[0, 1].map((i) => (
            <Skeleton key={i} className="h-28 rounded-[14px]" />
          ))}
        </div>
      ) : comparisons.isError ? (
        <div className="surface">
          <ErrorState error={comparisons.error} title="Comparisons could not be loaded" onRetry={() => void comparisons.refetch()} />
        </div>
      ) : list.length === 0 ? (
        <div className="surface">
          <EmptyState
            icon={GitCompareIcon}
            title="No comparisons yet"
            description="Compare two connections of the same engine to see what differs and generate a migration script. DBPilot never runs it for you."
            action={
              canConfirmWrites && sqlConnections.length >= 2 ? (
                <Button onClick={() => setNewOpen(true)}>
                  <PlusIcon />
                  Compare connections
                </Button>
              ) : undefined
            }
          />
        </div>
      ) : (
        <div className="space-y-3">
          {list.map((c) => (
            <ComparisonRow key={c.id} pid={pid} c={c} canConfirmWrites={canConfirmWrites} onRunPrepared={setConfirmation} />
          ))}
        </div>
      )}

      <Dialog open={newOpen} onOpenChange={(o) => !create.isPending && setNewOpen(o)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Compare two connections</DialogTitle>
            <DialogDescription>Each connection&apos;s latest schema snapshot is used; refresh it first if you changed the schema recently.</DialogDescription>
          </DialogHeader>
          <DialogBody className="space-y-4">
            <Field label="From">
              <SimpleSelect aria-label="From connection" value={fromId} onValueChange={setFromId} options={sqlConnections.map((c) => ({ value: c.id, label: c.name }))} />
            </Field>
            <Field label="To">
              <SimpleSelect aria-label="To connection" value={toId} onValueChange={setToId} options={sqlConnections.filter((c) => c.id !== fromId).map((c) => ({ value: c.id, label: c.name }))} />
            </Field>
          </DialogBody>
          <DialogFooter>
            <Button variant="ghost" onClick={() => setNewOpen(false)}>
              Cancel
            </Button>
            <Button onClick={submit} disabled={!fromId || !toId || create.isPending}>
              {create.isPending ? "Comparing..." : "Compare"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <ConfirmWriteDialog
        pid={pid}
        confirmation={confirmation}
        open={!!confirmation}
        onOpenChange={(o) => !o && setConfirmation(null)}
        onConfirmed={() => toast.message("The migration run is recorded in the audit log.")}
      />
    </>
  );
}

function ComparisonRow({
  pid,
  c,
  canConfirmWrites,
  onRunPrepared,
}: {
  pid: string;
  c: ComparisonOut;
  canConfirmWrites: boolean;
  onRunPrepared: (confirmation: ConfirmationCreated) => void;
}) {
  const run = useRunComparison(pid);
  const statements = c.statements ?? [];
  const notes = c.notes ?? [];

  return (
    <Surface>
      <SurfaceHeader
        title={
          <span className="flex items-center gap-2">
            <KindChip kind={c.engine} />
            {c.identical ? <Chip tone="success">Identical</Chip> : <Chip tone="warning">Differs</Chip>}
            {c.destructive && <Chip tone="danger">Destructive</Chip>}
          </span>
        }
        description={`Compared ${formatRelative(c.created_at)} by ${c.created_by_label}`}
        action={
          canConfirmWrites &&
          c.runnable && (
            <Button
              size="sm"
              disabled={run.isPending}
              onClick={() =>
                run.mutate(c.id, {
                  onSuccess: (confirmation) => onRunPrepared(confirmation),
                  onError: (err) => toast.error(errorMessage(err)),
                })
              }
            >
              {run.isPending ? "Preparing..." : "Run migration"}
            </Button>
          )
        }
      />
      <div className="px-5 pb-4">
        {c.identical ? (
          <p className="text-[13px] text-muted-foreground">These two connections&apos; schemas are identical.</p>
        ) : (
          <>
            {statements.length > 0 && (
              <pre className="overflow-x-auto rounded-xl bg-code-bg px-3.5 py-3 font-mono text-[12px] leading-relaxed text-code-fg">
                {c.migration_sql || statements.map((s) => s.sql).join("\n")}
              </pre>
            )}
            <p className="mt-2 text-[12.5px] text-muted-foreground">{plural(statements.length, "statement")} generated. DBPilot never runs this itself.</p>
            {!c.runnable && c.runnable_detail && (
              <p className="mt-1 flex items-center gap-1 text-[12px] text-warning">
                <AlertTriangleIcon className="size-3.5" />
                {c.runnable_detail}
              </p>
            )}
            {notes.length > 0 && (
              <ul className="mt-2 space-y-0.5 text-[12px] text-muted-foreground">
                {notes.map((n, i) => (
                  <li key={i}>{n}</li>
                ))}
              </ul>
            )}
          </>
        )}
      </div>
    </Surface>
  );
}
