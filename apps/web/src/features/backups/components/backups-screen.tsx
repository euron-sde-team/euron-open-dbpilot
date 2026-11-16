"use client";

import { ArchiveIcon, CheckCircle2Icon, DownloadIcon, PlusIcon, Trash2Icon, XCircleIcon } from "lucide-react";
import * as React from "react";
import { toast } from "sonner";

import { Hint } from "@/components/hint";
import { EmptyState } from "@/components/empty-state";
import { ErrorState } from "@/components/error-state";
import { RoleNote } from "@/components/role-note";
import { BackupStatusChip, KindChip } from "@/components/status-chips";
import { Surface, SurfaceHeader } from "@/components/surface";
import { Button } from "@/components/ui/button";
import { Dialog, DialogBody, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Field } from "@/components/ui/label";
import { SimpleSelect } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { downloadBackupUrl, useBackups, useCreateBackup, useDeleteBackup, useRequestRestore, useVerifyBackup } from "@/features/backups/api";
import { useConnections } from "@/features/connections/api";
import { ConfirmWriteDialog } from "@/features/confirmations/components/confirm-write-dialog";
import { useCurrentProject } from "@/features/projects/api";
import { errorMessage } from "@/lib/api/errors";
import type { BackupOut, ConfirmationCreated } from "@/lib/api/types";
import { formatBytes, formatRelative, plural } from "@/lib/format";

const BACKUP_SUPPORTED = new Set(["postgresql", "mysql", "sqlite", "mssql"]);

export function BackupsScreen() {
  const { pid, canConfirmWrites } = useCurrentProject();
  const backups = useBackups(pid);
  const connections = useConnections(pid);
  const create = useCreateBackup(pid);
  const [newOpen, setNewOpen] = React.useState(false);
  const [connectionId, setConnectionId] = React.useState("");
  const [restoring, setRestoring] = React.useState<ConfirmationCreated | null>(null);

  const eligible = (connections.data ?? []).filter((c) => BACKUP_SUPPORTED.has(c.kind));
  const list = backups.data?.items ?? [];

  function startBackup() {
    if (!connectionId) return;
    create.mutate(
      { connection_id: connectionId },
      {
        onSuccess: () => {
          toast.success("Backup started.");
          setNewOpen(false);
          setConnectionId("");
        },
        onError: (err) => toast.error(errorMessage(err)),
      },
    );
  }

  return (
    <>
      <div className="mb-6 flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <h1 className="text-[22px] leading-tight font-semibold tracking-[-0.02em]">Backups</h1>
          <p className="mt-1 max-w-2xl text-[13px] leading-relaxed text-muted-foreground">
            <span className="inline-flex items-center gap-1">
              Full dumps taken through each connector, never a raw shell command <Hint term="backup" />
            </span>
          </p>
        </div>
        {canConfirmWrites && eligible.length > 0 && (
          <Button onClick={() => setNewOpen(true)}>
            <PlusIcon />
            Back up now
          </Button>
        )}
      </div>

      {!canConfirmWrites && <RoleNote className="mb-4">Viewers see backups but cannot create, verify, delete or restore one. Ask a project admin.</RoleNote>}

      <div className="mb-4 space-y-1.5 rounded-xl bg-muted/70 px-4 py-2.5 text-[12.5px] text-muted-foreground">
        <p>Backup and restore work for PostgreSQL, MySQL, SQLite and SQL Server. MongoDB and Redis are not applicable to this feature in 0.1.0.</p>
        <p className="font-medium text-subtle">Scheduled backups: coming soon. Use &quot;Back up now&quot; until recurring schedules ship.</p>
      </div>

      {backups.isPending ? (
        <div className="space-y-3">
          {[0, 1, 2].map((i) => (
            <Skeleton key={i} className="h-20 rounded-[14px]" />
          ))}
        </div>
      ) : backups.isError ? (
        <div className="surface">
          <ErrorState error={backups.error} title="Backups could not be loaded" onRetry={() => void backups.refetch()} />
        </div>
      ) : list.length === 0 ? (
        <div className="surface">
          <EmptyState
            icon={ArchiveIcon}
            title="No backups yet"
            description="A manual backup captures a connection's current schema and rows. Restoring one needs its own confirmation, exactly like a write."
            action={
              canConfirmWrites && eligible.length > 0 ? (
                <Button onClick={() => setNewOpen(true)}>
                  <PlusIcon />
                  Back up now
                </Button>
              ) : undefined
            }
          />
        </div>
      ) : (
        <div className="space-y-3">
          {list.map((b) => (
            <BackupRow key={b.id} pid={pid} b={b} canConfirmWrites={canConfirmWrites} onRestorePrepared={setRestoring} />
          ))}
        </div>
      )}

      <Dialog open={newOpen} onOpenChange={(o) => !create.isPending && setNewOpen(o)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Back up a connection</DialogTitle>
            <DialogDescription>Runs immediately through the connector; the dump is checked for integrity automatically.</DialogDescription>
          </DialogHeader>
          <DialogBody>
            <Field label="Connection">
              <SimpleSelect aria-label="Connection to back up" value={connectionId} onValueChange={setConnectionId} options={eligible.map((c) => ({ value: c.id, label: c.name }))} />
            </Field>
          </DialogBody>
          <DialogFooter>
            <Button variant="ghost" onClick={() => setNewOpen(false)}>
              Cancel
            </Button>
            <Button onClick={startBackup} disabled={!connectionId || create.isPending}>
              {create.isPending ? "Starting..." : "Start backup"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <ConfirmWriteDialog
        pid={pid}
        confirmation={restoring}
        open={!!restoring}
        onOpenChange={(o) => !o && setRestoring(null)}
        onConfirmed={() => toast.message("The restore is recorded in the audit log.")}
      />
    </>
  );
}

function BackupRow({
  pid,
  b,
  canConfirmWrites,
  onRestorePrepared,
}: {
  pid: string;
  b: BackupOut;
  canConfirmWrites: boolean;
  onRestorePrepared: (c: ConfirmationCreated) => void;
}) {
  const verify = useVerifyBackup(pid);
  const del = useDeleteBackup(pid);
  const restore = useRequestRestore(pid);
  const [confirmingDelete, setConfirmingDelete] = React.useState(false);

  return (
    <Surface>
      <SurfaceHeader
        title={
          <span className="flex items-center gap-2">
            {b.connection_name}
            <KindChip kind={b.engine} />
            <BackupStatusChip status={b.status} />
          </span>
        }
        description={`Started ${formatRelative(b.started_at)}${b.finished_at ? `, finished ${formatRelative(b.finished_at)}` : ""}`}
        action={
          canConfirmWrites && (
            <div className="flex gap-1.5">
              {b.status === "succeeded" && (
                <>
                  <Button size="xs" variant="outline" render={<a href={downloadBackupUrl(pid, b.id)} download />}>
                    <DownloadIcon />
                    Download
                  </Button>
                  <Button
                    size="xs"
                    variant="outline"
                    disabled={verify.isPending}
                    onClick={() =>
                      verify.mutate(b.id, {
                        onSuccess: (r) => (r.integrity_ok ? toast.success(r.integrity_detail ?? "Integrity check passed.") : toast.error(r.integrity_detail ?? "Integrity check failed.")),
                        onError: (err) => toast.error(errorMessage(err)),
                      })
                    }
                  >
                    {verify.isPending ? "Verifying..." : "Verify"}
                  </Button>
                  <Button
                    size="xs"
                    disabled={restore.isPending}
                    onClick={() =>
                      restore.mutate(
                        { id: b.id, targetConnectionId: b.connection_id },
                        {
                          onSuccess: (confirmation) => onRestorePrepared(confirmation),
                          onError: (err) => toast.error(errorMessage(err)),
                        },
                      )
                    }
                  >
                    Restore
                  </Button>
                </>
              )}
              <Button size="xs" variant="ghost" onClick={() => setConfirmingDelete(true)}>
                <Trash2Icon />
              </Button>
            </div>
          )
        }
      />
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 px-5 pb-4 text-[12px] text-muted-foreground">
        <span>{b.size_bytes != null ? formatBytes(b.size_bytes) : "n/a"}</span>
        <span>{plural(b.table_count, "table")}</span>
        <span>{plural(b.row_count, "row")}</span>
        {b.integrity_ok != null && (
          <span className={`flex items-center gap-1 ${b.integrity_ok ? "text-savings" : "text-danger"}`}>
            {b.integrity_ok ? <CheckCircle2Icon className="size-3.5" /> : <XCircleIcon className="size-3.5" />}
            {b.integrity_ok ? "Integrity ok" : "Integrity check failed"}
          </span>
        )}
        {b.error && <span className="text-danger">{b.error}</span>}
      </div>
      <Dialog open={confirmingDelete} onOpenChange={setConfirmingDelete}>
        <DialogContent className="max-w-sm">
          <DialogHeader>
            <DialogTitle>Delete this backup?</DialogTitle>
            <DialogDescription>The file is removed. This cannot be undone.</DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="ghost" onClick={() => setConfirmingDelete(false)}>
              Cancel
            </Button>
            <Button
              variant="destructive"
              disabled={del.isPending}
              onClick={() =>
                del.mutate(b.id, {
                  onSuccess: () => {
                    setConfirmingDelete(false);
                    toast.success("Backup deleted.");
                  },
                  onError: (err) => toast.error(errorMessage(err)),
                })
              }
            >
              {del.isPending ? "Deleting..." : "Delete"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </Surface>
  );
}
