"use client";

import { AlertTriangleIcon, DownloadIcon } from "lucide-react";

import { Hint } from "@/components/hint";
import { QueryStatusChip } from "@/components/status-chips";
import { Button } from "@/components/ui/button";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import type { QueryResult } from "@/lib/api/types";
import { formatDuration, formatNumber } from "@/lib/format";

import { useExportUrl } from "../api";

function cellText(value: unknown): string {
  if (value === null || value === undefined) return "";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

/**
 * A query's outcome: AC3's refusal shown prominently (statement, error code and message),
 * never folded into a generic toast, and a real result table on success.
 */
export function ResultView({ pid, result }: { pid: string; result: QueryResult }) {
  const exportUrl = useExportUrl(pid, result.id, "csv");

  if (result.status === "refused") {
    return (
      <div className="space-y-3 rounded-xl bg-danger-soft p-4">
        <div className="flex items-center gap-2">
          <QueryStatusChip status={result.status} />
          {result.error_code && <code className="font-mono text-[11px] text-danger">{result.error_code}</code>}
        </div>
        <p className="text-[13px] font-medium text-danger">{result.error ?? "This statement was refused by the SQL guard before it reached the database."}</p>
        <pre className="overflow-x-auto rounded-lg bg-code-bg px-3 py-2.5 font-mono text-[12px] text-code-fg">{result.statement}</pre>
        <p className="flex items-center gap-1 text-[12px] text-danger">
          Never reached a database <Hint term="sqlGuard" />
        </p>
      </div>
    );
  }

  if (result.status !== "succeeded") {
    return (
      <div className="space-y-2 rounded-xl bg-warning-soft p-4">
        <div className="flex items-center gap-2">
          <QueryStatusChip status={result.status} />
        </div>
        <p className="text-[13px] text-warning">{result.error ?? "This query did not complete."}</p>
        {result.estimated_scan_rows != null && result.status === "scan_limit" && (
          <p className="flex items-center gap-1 text-[12px] text-warning">
            The planner expected {formatNumber(result.estimated_scan_rows)} rows, over this connection&apos;s scan limit <Hint term="scanLimit" />
          </p>
        )}
      </div>
    );
  }

  const page = result.page;
  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center justify-between gap-2 px-1">
        <div className="flex flex-wrap items-center gap-2 text-[12px] text-muted-foreground">
          <QueryStatusChip status={result.status} />
          <span>{formatNumber(result.row_count)} rows</span>
          <span>{formatDuration(result.duration_ms)}</span>
          {result.truncated && (
            <span className="flex items-center gap-1 text-warning">
              <AlertTriangleIcon className="size-3.5" />
              Truncated <Hint term="rowLimit" />
            </span>
          )}
        </div>
        <Button size="xs" variant="outline" render={<a href={exportUrl} download />}>
          <DownloadIcon />
          Export CSV
        </Button>
      </div>
      {!page || page.rows.length === 0 ? (
        <div className="rounded-xl bg-muted/70 px-4 py-6 text-center text-[13px] text-muted-foreground">No rows returned.</div>
      ) : (
        <div className="surface overflow-hidden">
          <Table>
            <TableHeader>
              <TableRow>
                {page.columns.map((c) => (
                  <TableHead key={c.name}>{c.name}</TableHead>
                ))}
              </TableRow>
            </TableHeader>
            <TableBody>
              {page.rows.map((row, ri) => (
                <TableRow key={ri}>
                  {row.map((cell, ci) => (
                    <TableCell key={ci} className="font-mono text-[12.5px]">
                      {cellText(cell)}
                    </TableCell>
                  ))}
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      )}
    </div>
  );
}
