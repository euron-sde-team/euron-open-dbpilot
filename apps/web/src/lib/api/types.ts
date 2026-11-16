/** Hand-picked aliases onto the generated schema, for readability at call sites. */
import type { components } from "./schema";

export type UserOut = components["schemas"]["UserOut"];
export type Role = UserOut["role"];

export type ProjectOut = components["schemas"]["ProjectOut"];
export type ProjectRole = ProjectOut["role"];

export type MemberOut = components["schemas"]["MemberOut"];

export type ConnectionOut = components["schemas"]["ConnectionOut"];
export type ConnectionKind = ConnectionOut["kind"];

export type GrantOut = components["schemas"]["GrantOut"];
export type MyAccess = components["schemas"]["MyAccess"];

export type SchemaObject = components["schemas"]["SchemaObject"];
export type SchemaColumn = components["schemas"]["SchemaColumn"];
export type SnapshotOut = components["schemas"]["SnapshotOut"];

export type QueryRunOut = components["schemas"]["QueryRunOut"];
export type QueryStatus = QueryRunOut["status"];
export type RowsPage = components["schemas"]["RowsPage"];
/** What POST .../queries/sql, .../mongodb, .../redis and .../ai/generations/{id}/run return: a QueryRunOut plus the first page of rows. */
export type QueryResult = components["schemas"]["QueryResult"];

export type GenerationOut = components["schemas"]["GenerationOut"];
export type GenerationStatus = GenerationOut["status"];
export type GuardOut = components["schemas"]["GuardOut"];

export type ConfirmationOut = components["schemas"]["ConfirmationOut"];
export type ConfirmationStatus = ConfirmationOut["status"];
export type ConfirmationKind = ConfirmationOut["kind"];
/**
 * What creating a write, restore or migration run returns: a ConfirmationOut plus a
 * one-time `confirm_token`. The token is deliberately absent from ConfirmationOut (what
 * GET /confirmations and /confirmations/{id} return), so it only ever exists in the
 * browser that just created the confirmation, never recoverable from a later list.
 */
export type ConfirmationCreated = components["schemas"]["ConfirmationCreated"];

export type BackupOut = components["schemas"]["BackupOut"];
export type BackupStatus = BackupOut["status"];

export type ComparisonOut = components["schemas"]["ComparisonOut"];

export type AuditEntryOut = components["schemas"]["AuditEntryOut"];

export type HealthCard = components["schemas"]["HealthCard"];
export type SecurityCheck = components["schemas"]["SecurityCheck"];

export type Totals = components["schemas"]["Totals"];
export type DashboardOut = components["schemas"]["DashboardOut"];
export type MetaOut = components["schemas"]["MetaOut"];
