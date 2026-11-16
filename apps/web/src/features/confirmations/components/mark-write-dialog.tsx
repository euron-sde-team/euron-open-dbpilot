"use client";

import * as React from "react";
import { toast } from "sonner";

import { Hint } from "@/components/hint";
import { Button } from "@/components/ui/button";
import { Dialog, DialogBody, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Textarea } from "@/components/ui/input";
import { Field, FieldError } from "@/components/ui/label";
import { errorMessage } from "@/lib/api/errors";
import type { ConfirmationCreated } from "@/lib/api/types";

import { useCreateWrite } from "../api";

/**
 * The first step of a human-marked write (AC5): never auto-detected, never the AI. The
 * person explicitly states the statement is a write; DBPilot describes it and returns a
 * pending confirmation, with nothing executed yet.
 */
export function MarkWriteDialog({
  pid,
  connectionId,
  initialSql,
  open,
  onOpenChange,
  onPrepared,
}: {
  pid: string;
  connectionId: string;
  initialSql: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onPrepared: (confirmation: ConfirmationCreated) => void;
}) {
  const [sql, setSql] = React.useState(initialSql);
  const [note, setNote] = React.useState("");
  const [error, setError] = React.useState<string | null>(null);
  const create = useCreateWrite(pid);

  React.useEffect(() => {
    if (open) {
      setSql(initialSql);
      setNote("");
      setError(null);
    }
  }, [open, initialSql]);

  function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!sql.trim()) return setError("Enter the statement you want to run as a write.");
    setError(null);
    create.mutate(
      { connection_id: connectionId, sql: sql.trim(), note: note.trim() },
      {
        onSuccess: (confirmation) => {
          onOpenChange(false);
          onPrepared(confirmation);
        },
        onError: (err) => {
          setError(errorMessage(err));
          toast.error(errorMessage(err));
        },
      },
    );
  }

  return (
    <Dialog open={open} onOpenChange={(o) => !create.isPending && onOpenChange(o)}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>Mark as a write</DialogTitle>
          <DialogDescription>
            <span className="inline-flex items-center gap-1">
              DBPilot never guesses this: you are stating this statement is an intentional write. Nothing runs yet <Hint term="humanWrite" />
            </span>
          </DialogDescription>
        </DialogHeader>
        <form onSubmit={submit} className="flex min-h-0 flex-col" noValidate>
          <DialogBody className="space-y-4">
            <Field label="Statement" htmlFor="mw-sql">
              <Textarea
                id="mw-sql"
                className="min-h-32 font-mono text-[12.5px]"
                value={sql}
                onChange={(e) => setSql(e.target.value)}
                placeholder="UPDATE orders SET status = 'cancelled' WHERE id = 42"
              />
            </Field>
            <Field label="Note" htmlFor="mw-note" hint="Optional. Why you are running this, for the audit log.">
              <Textarea id="mw-note" maxLength={2000} value={note} onChange={(e) => setNote(e.target.value)} />
            </Field>
            {error && <FieldError>{error}</FieldError>}
          </DialogBody>
          <DialogFooter>
            <Button type="button" variant="ghost" onClick={() => onOpenChange(false)}>
              Cancel
            </Button>
            <Button type="submit" variant="destructive" disabled={create.isPending}>
              {create.isPending ? "Preparing..." : "Review this write"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
