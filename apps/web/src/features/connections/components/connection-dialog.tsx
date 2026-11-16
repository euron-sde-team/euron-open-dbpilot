"use client";

import * as React from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Dialog, DialogBody, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input, Textarea } from "@/components/ui/input";
import { Field, FieldError, FieldHint } from "@/components/ui/label";
import { SimpleSelect } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { errorMessage } from "@/lib/api/errors";
import type { ConnectionKind } from "@/lib/api/types";
import { kindLabel } from "@/lib/status";

import { useCreateConnection, useCreateSqliteConnection, useTestDraftConnection } from "../api";

const SERVER_KINDS: ConnectionKind[] = ["postgresql", "mysql", "mssql", "mongodb", "redis"];

const DEFAULT_PORT: Record<ConnectionKind, number | null> = {
  postgresql: 5432,
  mysql: 3306,
  mssql: 1433,
  mongodb: 27017,
  redis: 6379,
  sqlite: null,
};

const SSL_OPTIONS = [
  { value: "disable", label: "Disable" },
  { value: "prefer", label: "Prefer" },
  { value: "require", label: "Require" },
  { value: "verify-full", label: "Verify full" },
];

interface DraftState {
  name: string;
  kind: ConnectionKind;
  description: string;
  host: string;
  port: string;
  database: string;
  username: string;
  password: string;
  ssl_mode: string;
  auth_source: string;
  max_rows: string;
  max_scan_rows: string;
  timeout_s: string;
  sample_values: boolean;
}

function emptyDraft(kind: ConnectionKind = "postgresql"): DraftState {
  return {
    name: "",
    kind,
    description: "",
    host: "",
    port: DEFAULT_PORT[kind] != null ? String(DEFAULT_PORT[kind]) : "",
    database: "",
    username: "",
    password: "",
    ssl_mode: "prefer",
    auth_source: "",
    max_rows: "1000",
    max_scan_rows: "10000000",
    timeout_s: "30",
    sample_values: false,
  };
}

/** Builds the ConnectionCreate body from the form, dropping fields the engine does not use. */
function bodyFrom(d: DraftState) {
  const body: Record<string, unknown> = {
    name: d.name.trim(),
    kind: d.kind,
    host: d.host.trim(),
    database: d.database.trim(),
    username: d.username.trim(),
    description: d.description.trim(),
    sample_values: d.sample_values,
  };
  const port = Number(d.port);
  if (d.port.trim() && Number.isFinite(port)) body.port = port;
  if (d.password) body.password = d.password;
  if (d.kind !== "mongodb" && d.kind !== "redis") body.ssl_mode = d.ssl_mode;
  if (d.kind === "mongodb" && d.auth_source.trim()) body.auth_source = d.auth_source.trim();
  const maxRows = Number(d.max_rows);
  if (Number.isFinite(maxRows)) body.max_rows = maxRows;
  const maxScan = Number(d.max_scan_rows);
  if (Number.isFinite(maxScan)) body.max_scan_rows = maxScan;
  const timeout = Number(d.timeout_s);
  if (Number.isFinite(timeout)) body.timeout_s = timeout;
  return body;
}

export function ConnectionDialog({ pid, open, onOpenChange }: { pid: string; open: boolean; onOpenChange: (open: boolean) => void }) {
  const [draft, setDraft] = React.useState<DraftState>(emptyDraft());
  const [sqliteFile, setSqliteFile] = React.useState<File | null>(null);
  const [error, setError] = React.useState<string | null>(null);
  const create = useCreateConnection(pid);
  const createSqlite = useCreateSqliteConnection(pid);
  const test = useTestDraftConnection(pid);

  React.useEffect(() => {
    if (open) {
      setDraft(emptyDraft());
      setSqliteFile(null);
      setError(null);
    }
  }, [open]);

  function set<K extends keyof DraftState>(key: K, value: DraftState[K]) {
    setDraft((d) => ({ ...d, [key]: value }));
  }

  function chooseKind(kind: ConnectionKind) {
    setDraft((d) => ({ ...emptyDraft(kind), name: d.name, description: d.description }));
  }

  function validate(): string | null {
    if (!draft.name.trim()) return "Name the connection, for example what it connects to.";
    if (draft.kind === "sqlite") {
      if (!sqliteFile) return "Choose a SQLite file to upload.";
      return null;
    }
    if (!draft.host.trim()) return "Enter the host DBPilot should reach this database on.";
    return null;
  }

  function doTest() {
    if (draft.kind === "sqlite") return;
    const v = validate();
    if (v) return setError(v);
    setError(null);
    test.mutate(bodyFrom(draft), {
      onSuccess: (r) => (r.ok ? toast.success(`${r.message} (${Math.round(r.latency_ms)} ms)`) : toast.error(r.message)),
      onError: (err) => toast.error(errorMessage(err)),
    });
  }

  function submit(e: React.FormEvent) {
    e.preventDefault();
    const v = validate();
    if (v) return setError(v);
    setError(null);
    if (draft.kind === "sqlite" && sqliteFile) {
      const form = new FormData();
      form.set("name", draft.name.trim());
      form.set("description", draft.description.trim());
      form.set("file", sqliteFile);
      createSqlite.mutate(form, {
        onSuccess: (c) => {
          toast.success(`${c.name} connected.`);
          onOpenChange(false);
        },
        onError: (err) => setError(errorMessage(err)),
      });
      return;
    }
    create.mutate(bodyFrom(draft), {
      onSuccess: (c) => {
        toast.success(`${c.name} connected.`);
        onOpenChange(false);
      },
      onError: (err) => setError(errorMessage(err)),
    });
  }

  const pending = create.isPending || createSqlite.isPending;
  const isServer = SERVER_KINDS.includes(draft.kind);

  return (
    <Dialog open={open} onOpenChange={(o) => !pending && onOpenChange(o)}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>New connection</DialogTitle>
          <DialogDescription>
            The password is sealed server side and never shown again. DBPilot opens the connection read-only wherever the engine
            supports it.
          </DialogDescription>
        </DialogHeader>
        <form onSubmit={submit} className="flex min-h-0 flex-col" noValidate>
          <DialogBody className="space-y-4">
            <Field label="Engine">
              <div className="grid grid-cols-3 gap-2 sm:grid-cols-6">
                {(["postgresql", "mysql", "sqlite", "mssql", "mongodb", "redis"] as ConnectionKind[]).map((k) => (
                  <button
                    key={k}
                    type="button"
                    onClick={() => chooseKind(k)}
                    className={`rounded-lg px-2 py-2 text-center text-[12px] font-medium transition-colors focus-visible:outline-2 focus-visible:outline-ring ${
                      draft.kind === k ? "bg-primary-soft text-primary" : "bg-muted text-muted-foreground hover:bg-accent"
                    }`}
                  >
                    {kindLabel(k)}
                  </button>
                ))}
              </div>
            </Field>

            <Field label="Name" htmlFor="cd-name">
              <Input id="cd-name" autoFocus maxLength={120} placeholder="Shop (production)" value={draft.name} onChange={(e) => set("name", e.target.value)} />
            </Field>

            {draft.kind === "sqlite" ? (
              <Field label="Database file" htmlFor="cd-file" hint="A SQLite database file, opened read-only mode plus PRAGMA query_only.">
                <input
                  id="cd-file"
                  type="file"
                  accept=".db,.sqlite,.sqlite3"
                  onChange={(e) => setSqliteFile(e.target.files?.[0] ?? null)}
                  className="block w-full rounded-lg bg-card text-sm text-foreground shadow-[var(--elev-1)] ring-1 ring-input ring-inset file:mr-3 file:rounded-md file:border-0 file:bg-muted file:px-3 file:py-1.5 file:text-[12.5px] file:font-medium"
                />
              </Field>
            ) : (
              <>
                <div className="grid grid-cols-[1fr_120px] gap-3">
                  <Field label="Host" htmlFor="cd-host">
                    <Input id="cd-host" maxLength={255} placeholder="db.internal" value={draft.host} onChange={(e) => set("host", e.target.value)} />
                  </Field>
                  <Field label="Port" htmlFor="cd-port">
                    <Input id="cd-port" type="number" min={1} max={65535} value={draft.port} onChange={(e) => set("port", e.target.value)} />
                  </Field>
                </div>
                <div className="grid grid-cols-2 gap-3">
                  <Field label="Database" htmlFor="cd-db" hint={draft.kind === "redis" ? "The database number, 0 to 15." : undefined}>
                    <Input id="cd-db" maxLength={128} value={draft.database} onChange={(e) => set("database", e.target.value)} />
                  </Field>
                  <Field label="Username" htmlFor="cd-user">
                    <Input id="cd-user" maxLength={128} value={draft.username} onChange={(e) => set("username", e.target.value)} />
                  </Field>
                </div>
                <Field label="Password" htmlFor="cd-pass" hint="Sealed server side; teammates never see it again.">
                  <Input id="cd-pass" type="password" autoComplete="new-password" value={draft.password} onChange={(e) => set("password", e.target.value)} />
                </Field>
                {draft.kind !== "mongodb" && draft.kind !== "redis" && (
                  <Field label="SSL mode">
                    <SimpleSelect aria-label="SSL mode" value={draft.ssl_mode} onValueChange={(v) => set("ssl_mode", v)} options={SSL_OPTIONS} />
                  </Field>
                )}
                {draft.kind === "mongodb" && (
                  <Field label="Auth source" htmlFor="cd-auth" hint="Optional. The database the username authenticates against, if different.">
                    <Input id="cd-auth" maxLength={128} value={draft.auth_source} onChange={(e) => set("auth_source", e.target.value)} />
                  </Field>
                )}
              </>
            )}

            <Field label="Description" htmlFor="cd-desc" hint="Optional. What this connection is for.">
              <Textarea id="cd-desc" maxLength={2000} value={draft.description} onChange={(e) => set("description", e.target.value)} />
            </Field>

            <div className="grid grid-cols-3 gap-3">
              <Field label="Max rows" htmlFor="cd-maxrows" hint="rowLimit">
                <Input id="cd-maxrows" type="number" min={1} max={100000} value={draft.max_rows} onChange={(e) => set("max_rows", e.target.value)} />
              </Field>
              <Field label="Max scan rows" htmlFor="cd-maxscan" hint="scanLimit">
                <Input id="cd-maxscan" type="number" min={1} value={draft.max_scan_rows} onChange={(e) => set("max_scan_rows", e.target.value)} />
              </Field>
              <Field label="Timeout (s)" htmlFor="cd-timeout" hint="timeout">
                <Input id="cd-timeout" type="number" min={1} max={600} value={draft.timeout_s} onChange={(e) => set("timeout_s", e.target.value)} />
              </Field>
            </div>

            {draft.kind !== "sqlite" && (
              <div className="flex items-center justify-between rounded-xl bg-muted/70 px-3.5 py-2.5">
                <div>
                  <div className="text-[13px] font-medium">Sample values</div>
                  <FieldHint>With this on, short text columns&apos; distinct values are read for the AI assistant&apos;s context.</FieldHint>
                </div>
                <Switch checked={draft.sample_values} onCheckedChange={(v) => set("sample_values", v === true)} />
              </div>
            )}

            {error && <FieldError>{error}</FieldError>}
          </DialogBody>
          <DialogFooter>
            {isServer && (
              <Button type="button" variant="outline" onClick={doTest} disabled={test.isPending || pending}>
                {test.isPending ? "Testing..." : "Test connection"}
              </Button>
            )}
            <Button type="button" variant="ghost" onClick={() => onOpenChange(false)}>
              Cancel
            </Button>
            <Button type="submit" disabled={pending}>
              {pending ? "Connecting..." : "Add connection"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
