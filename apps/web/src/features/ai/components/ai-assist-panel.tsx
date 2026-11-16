"use client";

import { ArrowUpIcon, PlayIcon, SparklesIcon } from "lucide-react";
import * as React from "react";
import { toast } from "sonner";

import { Hint } from "@/components/hint";
import { GuardBadge } from "@/components/status-chips";
import { Button } from "@/components/ui/button";
import { Chip } from "@/components/ui/chip";
import { Textarea } from "@/components/ui/input";
import { errorMessage } from "@/lib/api/errors";
import type { GenerationOut, QueryResult } from "@/lib/api/types";

import { useGenerateSql, useRunGeneration } from "../api";

/**
 * Natural-language-to-SQL, shown with the guard's verdict front and centre (AC4). A
 * generation the guard did not pass to "executable: true" never offers a run control
 * anywhere in this panel: that is the whole point of the feature.
 */
export function AiAssistPanel({
  pid,
  connectionId,
  onResult,
}: {
  pid: string;
  connectionId: string;
  onResult: (result: QueryResult) => void;
}) {
  const [question, setQuestion] = React.useState("");
  const [generation, setGeneration] = React.useState<GenerationOut | null>(null);
  const generate = useGenerateSql(pid);
  const run = useRunGeneration(pid);

  function submit(e: React.FormEvent) {
    e.preventDefault();
    const q = question.trim();
    if (!q || !connectionId) return;
    generate.mutate(
      { connection_id: connectionId, question: q },
      {
        onSuccess: (g) => setGeneration(g),
        onError: (err) => toast.error(errorMessage(err)),
      },
    );
  }

  function runIt() {
    if (!generation || !generation.executable) return;
    run.mutate(
      { generationId: generation.id },
      {
        onSuccess: (result) => {
          onResult(result);
          toast.success(`Ran in ${Math.round(result.duration_ms)} ms.`);
        },
        onError: (err) => toast.error(errorMessage(err)),
      },
    );
  }

  return (
    <div className="flex h-full min-h-0 flex-col gap-3">
      <div className="flex items-center gap-2 text-[13px] font-semibold">
        <SparklesIcon className="size-4 text-primary" />
        AI assistant
        <Hint term="aiNeverWrites" />
      </div>
      <form onSubmit={submit} className="flex items-end gap-2">
        <Textarea
          aria-label="Ask a question about this connection's data"
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              submit(e);
            }
          }}
          placeholder="How many orders were placed this month?"
          rows={2}
          className="min-h-16 resize-none text-[13px]"
          disabled={!connectionId}
        />
        <Button type="submit" size="icon" aria-label="Generate SQL" disabled={generate.isPending || !question.trim() || !connectionId}>
          <ArrowUpIcon />
        </Button>
      </form>

      <div className="min-h-0 flex-1 overflow-y-auto">
        {!connectionId ? (
          <p className="px-1 text-[12.5px] text-muted-foreground">Pick a SQL connection first.</p>
        ) : generate.isPending ? (
          <p className="px-1 text-[12.5px] text-muted-foreground">Writing a query against the real schema...</p>
        ) : generation ? (
          <GenerationCard generation={generation} onRun={runIt} running={run.isPending} />
        ) : (
          <p className="px-1 text-[12.5px] text-muted-foreground">Ask in plain English. Only a single read-only query the guard accepts can ever be run from here.</p>
        )}
      </div>
    </div>
  );
}

function GenerationCard({ generation, onRun, running }: { generation: GenerationOut; onRun: () => void; running: boolean }) {
  const tables = generation.tables ?? [];
  const unknownTables = generation.unknown_tables ?? [];
  const unknownColumns = generation.unknown_columns ?? [];
  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between gap-2">
        <GuardBadge passed={generation.guard.passed} />
        {tables.length > 0 && <span className="text-[11.5px] text-subtle">{tables.join(", ")}</span>}
      </div>
      {generation.sql && (
        <pre className="overflow-x-auto rounded-xl bg-code-bg px-3.5 py-3 font-mono text-[12px] leading-relaxed text-code-fg">{generation.sql}</pre>
      )}
      {!generation.guard.passed && (
        <div className="rounded-xl bg-danger-soft px-3.5 py-3 text-[12.5px] text-danger">
          <div className="font-semibold">Refused{generation.guard.code ? `: ${generation.guard.code}` : ""}</div>
          <p className="mt-0.5">{generation.guard.reason ?? "This statement is not a single read-only query, so it is refused for good. There is no override."}</p>
        </div>
      )}
      {generation.explanation && <p className="text-[12.5px] leading-relaxed text-muted-foreground">{generation.explanation}</p>}
      {(unknownTables.length > 0 || unknownColumns.length > 0) && (
        <div className="rounded-xl bg-warning-soft px-3.5 py-2.5 text-[12px] text-warning">
          Names not found in the real schema: {[...unknownTables, ...unknownColumns].join(", ")}
        </div>
      )}
      <div className="flex items-center justify-between">
        <span className="text-[11px] text-faint">
          {generation.provider}/{generation.model}, {Math.round(generation.latency_ms)} ms
        </span>
        {generation.executable ? (
          <Button size="sm" onClick={onRun} disabled={running}>
            <PlayIcon />
            {running ? "Running..." : "Run this query"}
          </Button>
        ) : (
          <Chip tone="neutral">No run control: refused for good</Chip>
        )}
      </div>
    </div>
  );
}
