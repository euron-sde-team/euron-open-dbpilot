"use client";

import { DatabaseIcon, PlayIcon, SparklesIcon } from "lucide-react";
import { useSearchParams } from "next/navigation";
import * as React from "react";
import { toast } from "sonner";

import { EmptyState } from "@/components/empty-state";
import { ErrorState } from "@/components/error-state";
import { Hint } from "@/components/hint";
import { PageHeader } from "@/components/page-header";
import { RoleNote } from "@/components/role-note";
import { KindChip } from "@/components/status-chips";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/input";
import { SimpleSelect } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { AiAssistPanel } from "@/features/ai/components/ai-assist-panel";
import { useConnections } from "@/features/connections/api";
import { ConfirmWriteDialog } from "@/features/confirmations/components/confirm-write-dialog";
import { MarkWriteDialog } from "@/features/confirmations/components/mark-write-dialog";
import { PendingConfirmations } from "@/features/confirmations/components/pending-confirmations";
import { useCurrentProject } from "@/features/projects/api";
import { errorMessage } from "@/lib/api/errors";
import type { ConfirmationCreated, ConnectionOut, QueryResult } from "@/lib/api/types";
import { isSqlEngine } from "@/lib/status";

import { useRunMongo, useRunRedis, useRunSql } from "../api";

import { ResultView } from "./result-view";

export function EditorScreen() {
  const { pid, canConfirmWrites } = useCurrentProject();
  const params = useSearchParams();
  const connections = useConnections(pid);
  const [connectionId, setConnectionId] = React.useState(params.get("connection") ?? "");
  const [aiOpen, setAiOpen] = React.useState(params.get("panel") === "ai");

  const list = React.useMemo(() => connections.data ?? [], [connections.data]);
  const connection = list.find((c) => c.id === connectionId) ?? list[0];
  React.useEffect(() => {
    if (!connectionId && list.length) setConnectionId(list[0]?.id ?? "");
  }, [connectionId, list]);

  if (connections.isPending) {
    return (
      <div className="space-y-3">
        <Skeleton className="h-10 w-72" />
        <Skeleton className="h-96 rounded-[14px]" />
      </div>
    );
  }
  if (connections.isError) {
    return (
      <div className="surface">
        <ErrorState error={connections.error} onRetry={() => void connections.refetch()} />
      </div>
    );
  }
  if (!list.length) {
    return (
      <div className="surface">
        <EmptyState
          icon={DatabaseIcon}
          title="Connect a database to query"
          description="The SQL editor and AI assistant work against a connection's real, discovered schema."
          action={
            <Button nativeButton={false} render={<a href={`/projects/${pid}/connections`} />}>
              <DatabaseIcon />
              Add a connection
            </Button>
          }
        />
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-4">
      <PageHeader
        title="SQL editor"
        description="Every statement runs through the SQL guard, then on a read-only session wherever the engine supports one."
        actions={
          <>
            <SimpleSelect
              aria-label="Connection"
              className="w-64"
              value={connection?.id ?? ""}
              onValueChange={setConnectionId}
              options={list.map((c) => ({ value: c.id, label: c.name }))}
            />
            {connection && <KindChip kind={connection.kind} />}
            {connection && isSqlEngine(connection.kind) && (
              <Button variant={aiOpen ? "default" : "outline"} size="sm" onClick={() => setAiOpen((v) => !v)}>
                <SparklesIcon />
                AI assist
              </Button>
            )}
          </>
        }
      />
      <PendingConfirmations pid={pid} />
      {connection && (
        <EngineWorkspace pid={pid} connection={connection} canConfirmWrites={canConfirmWrites} aiOpen={aiOpen} />
      )}
    </div>
  );
}

function EngineWorkspace({
  pid,
  connection,
  canConfirmWrites,
  aiOpen,
}: {
  pid: string;
  connection: ConnectionOut;
  canConfirmWrites: boolean;
  aiOpen: boolean;
}) {
  if (connection.kind === "mongodb") return <MongoPanel pid={pid} connection={connection} />;
  if (connection.kind === "redis") return <RedisPanel pid={pid} connection={connection} />;
  return <SqlPanel pid={pid} connection={connection} canConfirmWrites={canConfirmWrites} aiOpen={aiOpen} />;
}

function SqlPanel({
  pid,
  connection,
  canConfirmWrites,
  aiOpen,
}: {
  pid: string;
  connection: ConnectionOut;
  canConfirmWrites: boolean;
  aiOpen: boolean;
}) {
  const [sql, setSql] = React.useState("");
  const [result, setResult] = React.useState<QueryResult | null>(null);
  const [markOpen, setMarkOpen] = React.useState(false);
  const [confirming, setConfirming] = React.useState<ConfirmationCreated | null>(null);
  const run = useRunSql(pid);

  function runRead() {
    if (!sql.trim()) return;
    run.mutate(
      { connection_id: connection.id, sql: sql.trim() },
      {
        onSuccess: (r) => {
          setResult(r);
          if (r.status === "succeeded") toast.success(`${r.row_count} row${r.row_count === 1 ? "" : "s"} in ${Math.round(r.duration_ms)} ms.`);
          else if (r.status === "refused") toast.error("The SQL guard refused this statement. See the details below.");
        },
        onError: (err) => toast.error(errorMessage(err)),
      },
    );
  }

  return (
    <div className="grid min-w-0 gap-4" style={{ gridTemplateColumns: aiOpen ? "minmax(0,1fr) 360px" : "minmax(0,1fr)" }}>
      <div className="surface flex min-h-0 flex-col gap-3 p-4">
        <Textarea
          aria-label="SQL statement"
          value={sql}
          onChange={(e) => setSql(e.target.value)}
          placeholder={`SELECT * FROM orders LIMIT 20`}
          rows={6}
          className="min-h-32 font-mono text-[13px]"
        />
        <div className="flex flex-wrap items-center justify-between gap-2">
          <p className="flex items-center gap-1 text-[11.5px] text-subtle">
            Row limit {connection.max_rows}, scan limit {connection.max_scan_rows}, timeout {connection.timeout_s}s <Hint term="scanLimit" />
          </p>
          <div className="flex gap-2">
            {canConfirmWrites && connection.access.can_write && (
              <Button variant="outline" size="sm" onClick={() => setMarkOpen(true)} disabled={!sql.trim()}>
                Mark as a write
              </Button>
            )}
            <Button size="sm" onClick={runRead} disabled={run.isPending || !sql.trim()}>
              <PlayIcon />
              {run.isPending ? "Running..." : "Run"}
            </Button>
          </div>
        </div>
        {result && <ResultView pid={pid} result={result} />}
      </div>
      {aiOpen && (
        <div className="surface p-4">
          <AiAssistPanel pid={pid} connectionId={connection.id} onResult={setResult} />
        </div>
      )}
      <MarkWriteDialog
        pid={pid}
        connectionId={connection.id}
        initialSql={sql}
        open={markOpen}
        onOpenChange={setMarkOpen}
        onPrepared={setConfirming}
      />
      <ConfirmWriteDialog
        pid={pid}
        confirmation={confirming}
        open={!!confirming}
        onOpenChange={(o) => !o && setConfirming(null)}
        onConfirmed={() => toast.message("See the audit log for the full record of this write.")}
      />
    </div>
  );
}

function MongoPanel({ pid, connection }: { pid: string; connection: ConnectionOut }) {
  const [collection, setCollection] = React.useState("");
  const [filter, setFilter] = React.useState("{}");
  const [result, setResult] = React.useState<QueryResult | null>(null);
  const [filterError, setFilterError] = React.useState<string | null>(null);
  const run = useRunMongo(pid);

  function runIt() {
    if (!collection.trim()) return setFilterError("Enter a collection name.");
    let parsed: unknown;
    try {
      parsed = filter.trim() ? JSON.parse(filter) : {};
    } catch {
      return setFilterError("The filter must be valid JSON, like {\"status\": \"shipped\"}.");
    }
    setFilterError(null);
    run.mutate(
      { connection_id: connection.id, collection: collection.trim(), operation: "find", filter: parsed },
      {
        onSuccess: (r) => setResult(r),
        onError: (err) => toast.error(errorMessage(err)),
      },
    );
  }

  return (
    <div className="surface flex flex-col gap-3 p-4">
      <RoleNote>MongoDB has no read-only session setting, so DBPilot allows only read commands (find, aggregate, count, distinct) through an explicit allowlist.</RoleNote>
      <div className="grid gap-3 sm:grid-cols-[200px_minmax(0,1fr)]">
        <div className="flex flex-col gap-1.5">
          <label className="text-[13px] font-medium" htmlFor="mongo-collection">
            Collection
          </label>
          <input
            id="mongo-collection"
            className="h-9 rounded-lg bg-card px-3 text-sm shadow-[var(--elev-1)] ring-1 ring-input ring-inset"
            value={collection}
            onChange={(e) => setCollection(e.target.value)}
            placeholder="products"
          />
        </div>
        <div className="flex flex-col gap-1.5">
          <label className="text-[13px] font-medium" htmlFor="mongo-filter">
            Filter (JSON)
          </label>
          <Textarea id="mongo-filter" className="min-h-20 font-mono text-[12.5px]" value={filter} onChange={(e) => setFilter(e.target.value)} />
        </div>
      </div>
      {filterError && <p className="text-[12.5px] font-medium text-danger">{filterError}</p>}
      <div className="flex justify-end">
        <Button size="sm" onClick={runIt} disabled={run.isPending}>
          <PlayIcon />
          {run.isPending ? "Running..." : "Find"}
        </Button>
      </div>
      {result && <ResultView pid={pid} result={result} />}
    </div>
  );
}

const REDIS_READ_COMMANDS = ["GET", "MGET", "HGETALL", "HGET", "LRANGE", "SMEMBERS", "ZRANGE", "TTL", "TYPE", "KEYS", "SCAN", "EXISTS", "STRLEN"];

function RedisPanel({ pid, connection }: { pid: string; connection: ConnectionOut }) {
  const [command, setCommand] = React.useState("");
  const [argsText, setArgsText] = React.useState("");
  const [result, setResult] = React.useState<QueryResult | null>(null);
  const run = useRunRedis(pid);

  function runIt() {
    const cmd = command.trim().toUpperCase();
    if (!cmd) return;
    run.mutate(
      { connection_id: connection.id, command: `${cmd} ${argsText}`.trim() },
      {
        onSuccess: (r) => setResult(r),
        onError: (err) => toast.error(errorMessage(err)),
      },
    );
  }

  return (
    <div className="surface flex flex-col gap-3 p-4">
      <RoleNote>Redis has no read-only session setting, so DBPilot allows only read commands through an explicit allowlist: {REDIS_READ_COMMANDS.join(", ")}.</RoleNote>
      <div className="flex flex-wrap items-end gap-2">
        <div className="flex flex-col gap-1.5">
          <label className="text-[13px] font-medium" htmlFor="redis-command">
            Command and arguments
          </label>
          <div className="flex gap-2">
            <input
              id="redis-command"
              className="h-9 w-64 rounded-lg bg-card px-3 font-mono text-sm shadow-[var(--elev-1)] ring-1 ring-input ring-inset"
              value={command}
              onChange={(e) => setCommand(e.target.value)}
              placeholder="GET"
              list="redis-commands"
            />
            <datalist id="redis-commands">
              {REDIS_READ_COMMANDS.map((c) => (
                <option key={c} value={c} />
              ))}
            </datalist>
            <input
              className="h-9 flex-1 rounded-lg bg-card px-3 font-mono text-sm shadow-[var(--elev-1)] ring-1 ring-input ring-inset"
              value={argsText}
              onChange={(e) => setArgsText(e.target.value)}
              placeholder="session:42"
            />
          </div>
        </div>
        <Button size="sm" onClick={runIt} disabled={run.isPending || !command.trim()}>
          <PlayIcon />
          {run.isPending ? "Running..." : "Run"}
        </Button>
      </div>
      {result && <ResultView pid={pid} result={result} />}
    </div>
  );
}
