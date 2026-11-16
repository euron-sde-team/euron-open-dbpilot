"use client";

import { ClockIcon, EyeIcon, InfoIcon } from "lucide-react";
import * as React from "react";
import { toast } from "sonner";

import { ConfirmationStatusChip } from "@/components/status-chips";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Chip } from "@/components/ui/chip";
import { errorMessage } from "@/lib/api/errors";
import type { ConfirmationOut } from "@/lib/api/types";
import { formatRelative } from "@/lib/format";

import { useCancelConfirmation, useConfirmations } from "../api";

/**
 * A small banner of pending write/migration/restore confirmations, reusable anywhere.
 *
 * The confirm step needs a one-time token the API only ever returns on the call that
 * CREATED the confirmation (see ConfirmationCreated in lib/api/types.ts); a later GET
 * of the confirmations list, which is all this component has, never includes it, by
 * design, so nobody who merely has read access to this list can approve someone
 * else's pending write. A confirmation listed here can therefore only be reviewed
 * (read-only) or cancelled from this view; to actually run it, go back to where it was
 * created (the SQL editor for a write, the connection for a restore, the comparison for
 * a migration) and confirm it there, in the same step it was prepared.
 */
export function PendingConfirmations({ pid }: { pid: string }) {
  const q = useConfirmations(pid, "pending");
  const cancel = useCancelConfirmation(pid);
  const [reviewing, setReviewing] = React.useState<ConfirmationOut | null>(null);

  const pending = q.data?.items ?? [];
  if (!pending.length) return null;

  return (
    <div className="surface mb-4 overflow-hidden">
      <div className="flex items-center gap-2 bg-warning-soft px-4 py-2.5 text-[12.5px] font-semibold text-warning">
        <ClockIcon className="size-4" />
        {pending.length} pending confirmation{pending.length === 1 ? "" : "s"}
      </div>
      <ul className="divide-y divide-hairline px-4">
        {pending.map((c) => (
          <li key={c.id} className="flex flex-wrap items-center gap-3 py-2.5">
            <ConfirmationStatusChip status={c.status} />
            <span className="min-w-0 flex-1 truncate text-[13px]">
              {c.summary} on <span className="font-medium">{c.connection_name}</span>
            </span>
            <span className="shrink-0 text-[11.5px] text-subtle">{formatRelative(c.created_at)}</span>
            <div className="flex shrink-0 gap-1.5">
              <Button size="xs" variant="outline" onClick={() => setReviewing(c)}>
                <EyeIcon />
                Review
              </Button>
              <Button
                size="xs"
                variant="ghost"
                disabled={cancel.isPending}
                onClick={() => cancel.mutate(c.id, { onError: (err) => toast.error(errorMessage(err)) })}
              >
                Cancel
              </Button>
            </div>
          </li>
        ))}
      </ul>
      <ReviewOnlyDialog confirmation={reviewing} open={!!reviewing} onOpenChange={(o) => !o && setReviewing(null)} />
    </div>
  );
}

/**
 * Read-only view of a confirmation this component only knows from a list fetch, so it
 * has no confirm token (see the component doc above). Shows exactly what is pending,
 * same as the confirm dialog, but with no way to run it from here.
 */
function ReviewOnlyDialog({
  confirmation,
  open,
  onOpenChange,
}: {
  confirmation: ConfirmationOut | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  if (!confirmation) return null;
  const c = confirmation;
  const statements = c.statements ?? [];
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>Pending {c.kind}</DialogTitle>
          <DialogDescription>
            {c.summary} on <span className="font-medium text-foreground">{c.connection_name}</span>, requested by{" "}
            {c.requested_by_label ?? "a teammate"}.
          </DialogDescription>
        </DialogHeader>
        <DialogBody className="space-y-3">
          <div className="space-y-2">
            {statements.map((s) => (
              <div key={s.index} className="rounded-xl bg-code-bg px-3.5 py-3">
                <div className="flex items-center justify-between gap-2">
                  <span className="font-mono text-[11px] text-code-muted">statement {s.index + 1}</span>
                  <Chip tone={s.destructive ? "danger" : "neutral"}>{s.kind}</Chip>
                </div>
                <pre className="mt-1.5 overflow-x-auto whitespace-pre-wrap font-mono text-[12px] leading-relaxed text-code-fg">{s.sql}</pre>
              </div>
            ))}
          </div>
          <div className="flex items-start gap-2.5 rounded-xl bg-muted px-4 py-3 text-[12.5px] leading-relaxed text-muted-foreground">
            <InfoIcon className="mt-0.5 size-4 shrink-0 text-subtle" aria-hidden />
            <div>
              This is a read-only review. The one-time confirm token only exists in the browser that prepared this {c.kind}; to run
              it, go back to where it was created and confirm it in that same step, or cancel it here.
            </div>
          </div>
        </DialogBody>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Close
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
