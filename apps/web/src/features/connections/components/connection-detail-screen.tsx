"use client";

import {
  ActivityIcon,
  ChevronDownIcon,
  ChevronRightIcon,
  DatabaseIcon,
  KeyRoundIcon,
  LinkIcon,
  RefreshCwIcon,
  ShieldIcon,
  UsersIcon,
} from "lucide-react";
import * as React from "react";
import { toast } from "sonner";

import { EmptyState } from "@/components/empty-state";
import { ErrorState } from "@/components/error-state";
import { Hint } from "@/components/hint";
import { PageHeader } from "@/components/page-header";
import { RoleNote } from "@/components/role-note";
import { KindChip } from "@/components/status-chips";
import { Surface, SurfaceHeader } from "@/components/surface";
import { Button } from "@/components/ui/button";
import { Chip } from "@/components/ui/chip";
import { Field } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useCurrentProject } from "@/features/projects/api";
import { useMembers } from "@/features/projects/api";
import { errorMessage } from "@/lib/api/errors";
import type { SchemaObject } from "@/lib/api/types";
import { cn } from "@/lib/cn";
import { formatBytes, formatDuration, formatNumber, formatRelative, initials, plural } from "@/lib/format";
import { kindLabel, securityTone } from "@/lib/status";

import {
  useConnection,
  useConnectionHealth,
  useGrants,
  useRefreshSchema,
  useRelationships,
  useRemoveGrant,
  useSchema,
  useSetGrant,
} from "../api";

export function ConnectionDetailScreen({ cid }: { cid: string }) {
  const { pid, canManageConnections, role } = useCurrentProject();
  const connection = useConnection(pid, cid);

  if (connection.isPending) {
    return (
      <div className="space-y-4">
        <Skeleton className="h-10 w-72" />
        <Skeleton className="h-40 rounded-[14px]" />
        <Skeleton className="h-64 rounded-[14px]" />
      </div>
    );
  }
  if (connection.isError) {
    const missing = "status" in (connection.error as { status?: number }) && (connection.error as { status?: number }).status === 404;
    return (
      <div className="surface">
        {missing ? (
          <EmptyState
            icon={DatabaseIcon}
            title="This connection is not available"
            description="It may have been deleted, or you have no grant on it. Ask a project admin for access."
          />
        ) : (
          <ErrorState error={connection.error} title="This connection could not be opened" onRetry={() => void connection.refetch()} />
        )}
      </div>
    );
  }

  const c = connection.data;

  return (
    <>
      <PageHeader
        title={c.name}
        eyebrow={<KindChip kind={c.kind} />}
        description={c.description || `${kindLabel(c.kind)} connection.`}
      />
      <Tabs defaultValue="health">
        <TabsList>
          <TabsTrigger value="health">
            <ActivityIcon />
            Health
          </TabsTrigger>
          <TabsTrigger value="schema">
            <DatabaseIcon />
            Schema
          </TabsTrigger>
          <TabsTrigger value="relationships">
            <LinkIcon />
            Relationships
          </TabsTrigger>
          {(role === "admin" || role === "owner") && (
            <TabsTrigger value="grants">
              <UsersIcon />
              Access
            </TabsTrigger>
          )}
        </TabsList>
        <TabsContent value="health">
          <HealthTab pid={pid} cid={cid} />
        </TabsContent>
        <TabsContent value="schema">
          <SchemaTab pid={pid} cid={cid} canRefresh={canManageConnections} />
        </TabsContent>
        <TabsContent value="relationships">
          <RelationshipsTab pid={pid} cid={cid} kind={c.kind} />
        </TabsContent>
        {(role === "admin" || role === "owner") && (
          <TabsContent value="grants">
            <GrantsTab pid={pid} cid={cid} />
          </TabsContent>
        )}
      </Tabs>
    </>
  );
}

function HealthTab({ pid, cid }: { pid: string; cid: string }) {
  const q = useConnectionHealth(pid, cid);
  if (q.isPending) return <Skeleton className="h-64 rounded-[14px]" />;
  if (q.isError)
    return (
      <div className="surface">
        <ErrorState error={q.error} title="Health could not be checked" onRetry={() => void q.refetch()} />
      </div>
    );
  const h = q.data;
  const security = h.security ?? [];
  const summary = h.security_summary;
  const notes = h.notes ?? [];
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <div className="surface flex flex-col gap-1.5 p-4">
          <div className="text-[12.5px] font-medium text-muted-foreground">Reachable</div>
          <div className={cn("text-[18px] font-semibold", h.reachable ? "text-savings" : "text-danger")}>{h.reachable ? "Yes" : "No"}</div>
          {h.error && <div className="text-[12px] text-danger">{h.error}</div>}
        </div>
        <div className="surface flex flex-col gap-1.5 p-4">
          <div className="flex items-center gap-1 text-[12.5px] font-medium text-muted-foreground">
            Read-only session
            <Hint term="readOnlySession" />
          </div>
          <div className="text-[18px] font-semibold">{h.read_only_session == null ? "Allowlist only" : h.read_only_session ? "Confirmed" : "Not supported"}</div>
        </div>
        <div className="surface flex flex-col gap-1.5 p-4">
          <div className="text-[12.5px] font-medium text-muted-foreground">Latency</div>
          <div className="text-[18px] font-semibold tabular">{formatDuration(h.latency_ms)}</div>
        </div>
        <div className="surface flex flex-col gap-1.5 p-4">
          <div className="text-[12.5px] font-medium text-muted-foreground">Size</div>
          <div className="text-[18px] font-semibold tabular">{h.size_bytes != null ? formatBytes(h.size_bytes) : "n/a"}</div>
        </div>
      </div>

      <Surface>
        <SurfaceHeader
          title="Weak configuration check"
          description={<span className="inline-flex items-center gap-1">Read from the connected engine itself, not a guess <Hint term="weakConfig" /></span>}
          action={
            <div className="flex items-center gap-1.5 text-[12px] text-muted-foreground">
              <Chip tone="success">{summary?.passed ?? 0} pass</Chip>
              {!!summary?.warnings && summary.warnings > 0 && <Chip tone="warning">{summary.warnings} warn</Chip>}
              {!!summary?.failures && summary.failures > 0 && <Chip tone="danger">{summary.failures} fail</Chip>}
            </div>
          }
        />
        {security.length === 0 ? (
          <div className="px-5 pb-5 text-[13px] text-muted-foreground">No checks reported for this engine yet.</div>
        ) : (
          <ul className="divide-y divide-hairline px-5 pb-2">
            {security.map((check) => (
              <li key={check.id} className="flex items-start gap-3 py-2.5">
                <Chip tone={securityTone(check.status)} className="mt-0.5 shrink-0">
                  {check.status}
                </Chip>
                <div className="min-w-0">
                  <div className="text-[13px] font-medium">{check.title}</div>
                  <div className="text-[12.5px] text-muted-foreground">{check.detail}</div>
                  {check.setting && <div className="mt-0.5 font-mono text-[11px] text-subtle">{check.setting}</div>}
                </div>
              </li>
            ))}
          </ul>
        )}
      </Surface>

      {notes.length > 0 && (
        <Surface>
          <SurfaceHeader title="Notes" />
          <ul className="space-y-1 px-5 pb-4 text-[12.5px] text-muted-foreground">
            {notes.map((n, i) => (
              <li key={i}>{n}</li>
            ))}
          </ul>
        </Surface>
      )}

      <p className="text-[11.5px] text-faint">Checked {formatRelative(h.checked_at)}{h.cached ? `, served from a ${h.cache_ttl_s}s cache` : ""}.</p>
    </div>
  );
}

function SchemaTab({ pid, cid, canRefresh }: { pid: string; cid: string; canRefresh: boolean }) {
  const q = useSchema(pid, cid);
  const refresh = useRefreshSchema(pid, cid);
  if (q.isPending) return <Skeleton className="h-64 rounded-[14px]" />;
  if (q.isError)
    return (
      <div className="surface">
        <ErrorState error={q.error} title="Schema could not be loaded" onRetry={() => void q.refetch()} />
      </div>
    );
  const snap = q.data;
  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <p className="text-[12.5px] text-muted-foreground">
          {plural(snap.object_count, "object")}, {plural(snap.column_count, "column")}. Captured {formatRelative(snap.captured_at)}
          {snap.cached ? ", from cache" : ""}.
          <Hint term="schemaSnapshot" className="ml-1" />
        </p>
        {canRefresh && (
          <Button
            size="sm"
            variant="outline"
            disabled={refresh.isPending}
            onClick={() =>
              refresh.mutate(undefined, {
                onSuccess: () => toast.success("Schema re-read from the live connection."),
                onError: (err) => toast.error(errorMessage(err)),
              })
            }
          >
            <RefreshCwIcon className={cn(refresh.isPending && "animate-spin")} />
            {refresh.isPending ? "Reading..." : "Refresh"}
          </Button>
        )}
      </div>
      {snap.objects.length === 0 ? (
        <div className="surface">
          <EmptyState icon={DatabaseIcon} title="No objects found" description="This connection's schema has no tables, views, collections or key groups visible to DBPilot's login." />
        </div>
      ) : (
        <div className="space-y-2">
          {snap.objects.map((o) => (
            <SchemaObjectRow key={`${o.schema_name ?? ""}.${o.name}`} object={o} />
          ))}
        </div>
      )}
    </div>
  );
}

function SchemaObjectRow({ object: o }: { object: SchemaObject }) {
  const [open, setOpen] = React.useState(false);
  const columns = o.columns ?? [];
  const foreignKeys = o.foreign_keys ?? [];
  const indexes = o.indexes ?? [];
  return (
    <div className="surface overflow-hidden">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="flex w-full items-center gap-3 px-4 py-3 text-left hover:bg-muted/50 focus-visible:outline-2 focus-visible:outline-ring"
      >
        {open ? <ChevronDownIcon className="size-4 shrink-0 text-subtle" /> : <ChevronRightIcon className="size-4 shrink-0 text-subtle" />}
        <span className="min-w-0 flex-1">
          <span className="flex items-center gap-2">
            <span className="truncate font-mono text-[13px] font-medium">{o.schema_name ? `${o.schema_name}.${o.name}` : o.name}</span>
            <Chip tone="neutral">{o.kind}</Chip>
          </span>
        </span>
        {o.row_count != null && <span className="shrink-0 text-[12px] text-subtle">{formatNumber(o.row_count)} rows</span>}
      </button>
      {open && (
        <div className="border-t border-hairline px-4 py-3">
          {columns.length > 0 && (
            <div className="space-y-1">
              {columns.map((col) => (
                <div key={col.name} className="flex flex-wrap items-center gap-2 py-1 text-[12.5px]">
                  <span className="font-mono font-medium">{col.name}</span>
                  <span className="text-subtle">{col.type}</span>
                  {col.primary_key && <Chip tone="primary">PK</Chip>}
                  {!col.nullable && <Chip tone="neutral">not null</Chip>}
                  {col.autoincrement && <Chip tone="info">auto</Chip>}
                </div>
              ))}
            </div>
          )}
          {foreignKeys.length > 0 && (
            <div className="mt-2 space-y-1 border-t border-hairline pt-2">
              {foreignKeys.map((fk, i) => (
                <div key={i} className="text-[12px] text-muted-foreground">
                  {fk.columns.join(", ")} &rarr; {fk.ref_table}({fk.ref_columns.join(", ")})
                </div>
              ))}
            </div>
          )}
          {indexes.length > 0 && (
            <div className="mt-2 space-y-1 border-t border-hairline pt-2">
              {indexes.map((idx) => (
                <div key={idx.name} className="text-[12px] text-muted-foreground">
                  {idx.name}: {idx.columns.join(", ")}
                  {idx.unique ? " (unique)" : ""}
                </div>
              ))}
            </div>
          )}
          {columns.length === 0 && foreignKeys.length === 0 && indexes.length === 0 && (
            <p className="text-[12.5px] text-muted-foreground">No columns, foreign keys or indexes reported for this object.</p>
          )}
        </div>
      )}
    </div>
  );
}

function RelationshipsTab({ pid, cid, kind }: { pid: string; cid: string; kind: string }) {
  const supported = kind === "postgresql" || kind === "mysql" || kind === "sqlite" || kind === "mssql";
  const q = useRelationships(pid, cid, supported);
  if (!supported) {
    return (
      <div className="surface">
        <EmptyState icon={LinkIcon} title="Not applicable to this engine" description={`${kindLabel(kind)} has no foreign-key relationships for DBPilot to diagram. Use the schema tab to browse its objects.`} />
      </div>
    );
  }
  if (q.isPending) return <Skeleton className="h-64 rounded-[14px]" />;
  if (q.isError)
    return (
      <div className="surface">
        <ErrorState error={q.error} title="Relationships could not be loaded" onRetry={() => void q.refetch()} />
      </div>
    );
  const r = q.data;
  if (r.edges.length === 0) {
    return (
      <div className="surface">
        <EmptyState icon={LinkIcon} title="No foreign keys found" description="This schema's tables are not linked by a foreign key DBPilot can see." />
      </div>
    );
  }
  return (
    <div className="surface">
      <SurfaceHeader title="Foreign keys" description={`${plural(r.nodes.length, "table")}, ${plural(r.edges.length, "relationship")}.`} />
      <ul className="divide-y divide-hairline px-5 pb-2">
        {r.edges.map((e, i) => (
          <li key={i} className="flex flex-wrap items-center gap-2 py-2.5 font-mono text-[12.5px]">
            <span className="font-medium">{e.source}</span>
            <span className="text-subtle">({e.source_columns.join(", ")})</span>
            <span className="text-primary">&rarr;</span>
            <span className="font-medium">{e.target}</span>
            <span className="text-subtle">({e.target_columns.join(", ")})</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

function GrantsTab({ pid, cid }: { pid: string; cid: string }) {
  const grants = useGrants(pid, cid);
  const members = useMembers(pid);
  const setGrant = useSetGrant(pid, cid);
  const removeGrant = useRemoveGrant(pid, cid);
  const [pickedUser, setPickedUser] = React.useState("");

  if (grants.isPending || members.isPending) return <Skeleton className="h-64 rounded-[14px]" />;
  if (grants.isError)
    return (
      <div className="surface">
        <ErrorState error={grants.error} title="Grants could not be loaded" onRetry={() => void grants.refetch()} />
      </div>
    );

  const grantList = grants.data ?? [];
  const allMembersGrant = grantList.find((g) => g.all_members);
  const grantedUserIds = new Set(grantList.filter((g) => g.user_id).map((g) => g.user_id));
  const ungranted = (members.data ?? []).filter((m) => !grantedUserIds.has(m.user_id));

  return (
    <div className="space-y-4">
      <RoleNote>
        <span className="inline-flex items-center gap-1">
          A project member with no grant here cannot see this connection at all <Hint term="grant" />
        </span>
      </RoleNote>

      <Surface>
        <SurfaceHeader title="All project members" description="Grant every current and future member access at once." />
        <div className="flex items-center justify-between px-5 pb-4">
          <div className="flex items-center gap-2 text-[13px]">
            <ShieldIcon className="size-4 text-subtle" />
            {allMembersGrant ? "Granted to all members" : "Not granted to all members"}
          </div>
          <div className="flex items-center gap-3">
            {allMembersGrant && (
              <label className="flex items-center gap-2 text-[12.5px] text-muted-foreground">
                <Hint term="canWrite" />
                Can write
                <Switch
                  checked={allMembersGrant.can_write}
                  onCheckedChange={(v) =>
                    setGrant.mutate({ user_id: null, can_write: v === true }, { onError: (err) => toast.error(errorMessage(err)) })
                  }
                />
              </label>
            )}
            <Button
              size="sm"
              variant={allMembersGrant ? "outline" : "default"}
              onClick={() => {
                if (allMembersGrant) removeGrant.mutate(allMembersGrant.id, { onError: (err) => toast.error(errorMessage(err)) });
                else setGrant.mutate({ user_id: null, can_write: false }, { onError: (err) => toast.error(errorMessage(err)) });
              }}
            >
              {allMembersGrant ? "Revoke" : "Grant all members"}
            </Button>
          </div>
        </div>
      </Surface>

      <Surface>
        <SurfaceHeader title="Individual grants" />
        {grantList.filter((g) => !g.all_members).length === 0 ? (
          <div className="px-5 pb-4 text-[13px] text-muted-foreground">No individual grants yet.</div>
        ) : (
          <ul className="divide-y divide-hairline px-5">
            {grantList
              .filter((g) => !g.all_members)
              .map((g) => (
                <li key={g.id} className="flex items-center gap-3 py-2.5">
                  <span className="flex size-8 shrink-0 items-center justify-center rounded-full bg-primary-soft text-[11px] font-semibold text-primary">{initials(g.name)}</span>
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-[13px] font-medium">{g.name ?? g.email ?? "Unknown"}</span>
                    <span className="block truncate text-[12px] text-subtle">{g.email}</span>
                  </span>
                  <label className="flex items-center gap-2 text-[12px] text-muted-foreground">
                    Can write
                    <Switch
                      checked={g.can_write}
                      onCheckedChange={(v) =>
                        setGrant.mutate({ user_id: g.user_id, can_write: v === true }, { onError: (err) => toast.error(errorMessage(err)) })
                      }
                    />
                  </label>
                  <Button size="sm" variant="ghost" onClick={() => removeGrant.mutate(g.id, { onError: (err) => toast.error(errorMessage(err)) })}>
                    Revoke
                  </Button>
                </li>
              ))}
          </ul>
        )}
        {ungranted.length > 0 && (
          <div className="flex items-center gap-2 px-5 py-3">
            <Field label="Add a member" className="flex-1">
              <select
                className="h-9 w-full rounded-lg bg-card px-3 text-sm shadow-[var(--elev-1)] ring-1 ring-input ring-inset"
                value={pickedUser}
                onChange={(e) => setPickedUser(e.target.value)}
              >
                <option value="">Choose a member</option>
                {ungranted.map((m) => (
                  <option key={m.user_id} value={m.user_id}>
                    {m.name} ({m.email})
                  </option>
                ))}
              </select>
            </Field>
            <Button
              className="mt-5"
              disabled={!pickedUser || setGrant.isPending}
              onClick={() => {
                setGrant.mutate(
                  { user_id: pickedUser, can_write: false },
                  {
                    onSuccess: () => {
                      toast.success("Grant added.");
                      setPickedUser("");
                    },
                    onError: (err) => toast.error(errorMessage(err)),
                  },
                );
              }}
            >
              <KeyRoundIcon />
              Grant
            </Button>
          </div>
        )}
      </Surface>
    </div>
  );
}
