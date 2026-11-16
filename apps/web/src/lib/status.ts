import type { BackupStatus, ConfirmationStatus, ConnectionKind, GenerationStatus, QueryStatus } from "./api/types";

/** The tone a state is drawn in. Always shown with an icon and a word, never by color alone. */
export type Tone = "success" | "warning" | "danger" | "info" | "primary" | "neutral";

const QUERY_LABEL: Record<QueryStatus, string> = {
  succeeded: "Succeeded",
  refused: "Refused by the guard",
  failed: "Failed",
  timeout: "Timed out",
  scan_limit: "Over the scan limit",
};

const QUERY_TONE: Record<QueryStatus, Tone> = {
  succeeded: "success",
  refused: "danger",
  failed: "danger",
  timeout: "warning",
  scan_limit: "warning",
};

export function queryLabel(status: string): string {
  return QUERY_LABEL[status as QueryStatus] ?? status;
}

export function queryTone(status: string): Tone {
  return QUERY_TONE[status as QueryStatus] ?? "neutral";
}

const GENERATION_LABEL: Record<GenerationStatus, string> = {
  accepted: "Accepted",
  refused: "Refused",
  no_sql: "No SQL generated",
  failed: "Failed",
  answered: "Answered",
};

const GENERATION_TONE: Record<GenerationStatus, Tone> = {
  accepted: "success",
  refused: "danger",
  no_sql: "warning",
  failed: "danger",
  answered: "success",
};

export function generationLabel(status: string): string {
  return GENERATION_LABEL[status as GenerationStatus] ?? status;
}

export function generationTone(status: string): Tone {
  return GENERATION_TONE[status as GenerationStatus] ?? "neutral";
}

const CONFIRMATION_LABEL: Record<ConfirmationStatus, string> = {
  pending: "Pending confirmation",
  executing: "Executing",
  executed: "Executed",
  failed: "Failed",
  cancelled: "Cancelled",
  expired: "Expired",
};

const CONFIRMATION_TONE: Record<ConfirmationStatus, Tone> = {
  pending: "warning",
  executing: "primary",
  executed: "success",
  failed: "danger",
  cancelled: "neutral",
  expired: "neutral",
};

export function confirmationLabel(status: string): string {
  return CONFIRMATION_LABEL[status as ConfirmationStatus] ?? status;
}

export function confirmationTone(status: string): Tone {
  return CONFIRMATION_TONE[status as ConfirmationStatus] ?? "neutral";
}

const BACKUP_LABEL: Record<BackupStatus, string> = {
  running: "Running",
  succeeded: "Succeeded",
  failed: "Failed",
};

const BACKUP_TONE: Record<BackupStatus, Tone> = {
  running: "primary",
  succeeded: "success",
  failed: "danger",
};

export function backupLabel(status: string): string {
  return BACKUP_LABEL[status as BackupStatus] ?? status;
}

export function backupTone(status: string): Tone {
  return BACKUP_TONE[status as BackupStatus] ?? "neutral";
}

export const KIND_LABEL: Record<ConnectionKind, string> = {
  postgresql: "PostgreSQL",
  mysql: "MySQL",
  sqlite: "SQLite",
  mssql: "SQL Server",
  mongodb: "MongoDB",
  redis: "Redis",
};

export function kindLabel(kind: string): string {
  return KIND_LABEL[kind as ConnectionKind] ?? kind;
}

/** Engines that speak SQL through the editor; Mongo/Redis get their own query surfaces. */
export function isSqlEngine(kind: string): boolean {
  return kind === "postgresql" || kind === "mysql" || kind === "sqlite" || kind === "mssql";
}

const SECURITY_TONE: Record<string, Tone> = { pass: "success", warn: "warning", fail: "danger", info: "info", unknown: "neutral" };

export function securityTone(status: string): Tone {
  return SECURITY_TONE[status] ?? "neutral";
}
