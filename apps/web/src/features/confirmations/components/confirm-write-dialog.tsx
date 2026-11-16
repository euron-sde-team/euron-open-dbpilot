"use client";

import { AlertTriangleIcon, ShieldAlertIcon } from "lucide-react";
import * as React from "react";
import { toast } from "sonner";

import { Hint } from "@/components/hint";
import { Button } from "@/components/ui/button";
import { Chip } from "@/components/ui/chip";
import { Dialog, DialogBody, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { errorMessage } from "@/lib/api/errors";
import type { ConfirmationCreated } from "@/lib/api/types";

import { useConfirm } from "../api";

/**
 * The second, separate step of a confirmed write, migration or restore (AC5/AC7/AC8):
 * shows exactly what will run and its digest, then sends the one-time confirm token
 * back to prove this is the same browser session that just saw the preview. The token
 * only exists on the response that CREATED the confirmation (ConfirmationCreated); it
 * is never returned by a later GET, by design, so this dialog only ever opens right
 * after that creation call, never from a confirmation fetched off a list.
 */
export function ConfirmWriteDialog({
  pid,
  confirmation,
  open,
  onOpenChange,
  onConfirmed,
}: {
  pid: string;
  confirmation: ConfirmationCreated | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onConfirmed?: (queryId: string | null) => void;
}) {
  const confirm = useConfirm(pid);

  if (!confirmation) return null;
  // Narrowed to a local const: a nested function closing over the prop loses the
  // null-check TypeScript already did above.
  const c = confirmation;
  const statements = c.statements ?? [];
  const warnings = c.warnings ?? [];

  const title = c.kind === "write" ? "Confirm this write" : c.kind === "migration" ? "Confirm this migration" : "Confirm this restore";
  const destructive = statements.some((s) => s.destructive);

  function doConfirm() {
    confirm.mutate(
      { id: c.id, confirmToken: c.confirm_token, digest: c.digest },
      {
        onSuccess: (result) => {
          toast.success(c.kind === "write" ? "Write executed and recorded in the audit log." : c.kind === "migration" ? "Migration executed and recorded in the audit log." : "Restore executed and recorded in the audit log.");
          onOpenChange(false);
          onConfirmed?.(result.query_id ?? null);
        },
        onError: (err) => toast.error(errorMessage(err)),
      },
    );
  }

  return (
    <Dialog open={open} onOpenChange={(o) => !confirm.isPending && onOpenChange(o)}>
      <DialogContent className="max-w-lg" showCloseButton={false}>
        <DialogHeader className="pr-6">
          <DialogTitle className="flex items-center gap-2">
            {destructive && <ShieldAlertIcon className="size-4 text-danger" />}
            {title}
          </DialogTitle>
          <DialogDescription>
            {c.summary} on <span className="font-medium text-foreground">{c.connection_name}</span>. This is
            exactly what will run; nothing has executed yet.
          </DialogDescription>
        </DialogHeader>
        <DialogBody className="space-y-3">
          <div className="space-y-2">
            {statements.map((s) => (
              <div key={s.index} className="rounded-xl bg-code-bg px-3.5 py-3">
                <div className="flex items-center justify-between gap-2">
                  <span className="font-mono text-[11px] text-code-muted">statement {s.index + 1}</span>
                  <div className="flex items-center gap-1.5">
                    <Chip tone="neutral">{s.kind}</Chip>
                    {s.destructive && <Chip tone="danger">destructive</Chip>}
                  </div>
                </div>
                <pre className="mt-1.5 overflow-x-auto whitespace-pre-wrap font-mono text-[12px] leading-relaxed text-code-fg">{s.sql}</pre>
                {s.summary && <p className="mt-1 text-[11.5px] text-code-muted">{s.summary}</p>}
              </div>
            ))}
          </div>
          {warnings.length > 0 && (
            <div className="rounded-xl bg-warning-soft px-3.5 py-3">
              <div className="flex items-center gap-1.5 text-[12.5px] font-semibold text-warning">
                <AlertTriangleIcon className="size-3.5" />
                Warnings
              </div>
              <ul className="mt-1 space-y-0.5 text-[12px] text-warning">
                {warnings.map((w, i) => (
                  <li key={i}>{w}</li>
                ))}
              </ul>
            </div>
          )}
          <p className="flex items-center gap-1 text-[11.5px] text-subtle">
            Digest: <code className="font-mono">{c.digest.slice(0, 16)}...</code>
            <Hint term="digest" />
          </p>
        </DialogBody>
        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)} disabled={confirm.isPending}>
            Cancel
          </Button>
          <Button variant={destructive ? "destructive" : "default"} onClick={doConfirm} disabled={confirm.isPending}>
            {confirm.isPending ? "Running..." : "Confirm and run"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
