"use client";

import { ArrowRightIcon, DatabaseIcon, FolderPlusIcon, ScrollTextIcon, ShieldCheckIcon, WorkflowIcon } from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import * as React from "react";
import { toast } from "sonner";

import { ErrorState } from "@/components/error-state";
import { PageHeader } from "@/components/page-header";
import { Button } from "@/components/ui/button";
import { Chip } from "@/components/ui/chip";
import { Dialog, DialogBody, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input, Textarea } from "@/components/ui/input";
import { Field, FieldError } from "@/components/ui/label";
import { SimpleSelect } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { useOrgs } from "@/features/orgs/api";
import { errorMessage } from "@/lib/api/errors";
import type { ProjectOut } from "@/lib/api/types";
import { cn } from "@/lib/cn";
import { formatNumber } from "@/lib/format";
import { PROJECT_ROLE_LABEL } from "@/lib/roles";

import { useCreateProject, useProjects } from "../api";

export function ProjectsScreen() {
  const q = useProjects();
  const orgs = useOrgs();
  const params = useSearchParams();
  const router = useRouter();
  const pathname = usePathname();
  const [open, setOpen] = React.useState(false);

  React.useEffect(() => {
    if (params.get("new") === "1") setOpen(true);
  }, [params]);

  function setDialog(next: boolean) {
    setOpen(next);
    if (!next && params.get("new")) router.replace(pathname, { scroll: false });
  }

  const orgList = orgs.data ?? [];
  const projects = q.data ?? [];
  const canCreate = orgList.some((o) => o.role === "admin" || o.role === "owner");

  return (
    <>
      <PageHeader
        title="Projects"
        description="A project holds one team's connections, queries, backups and audit log. Every teammate added to it signs in to DBPilot, never to the raw database."
        actions={
          projects.length > 0 &&
          canCreate && (
            <Button onClick={() => setDialog(true)}>
              <FolderPlusIcon />
              New project
            </Button>
          )
        }
      />
      {q.isPending || orgs.isPending ? (
        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3" aria-busy aria-label="Loading projects">
          {Array.from({ length: 3 }).map((_, i) => (
            <Skeleton key={i} className="h-40 rounded-[14px]" />
          ))}
        </div>
      ) : q.isError || orgs.isError ? (
        <div className="surface">
          <ErrorState
            error={q.error ?? orgs.error}
            title="Projects could not be loaded"
            onRetry={() => {
              void q.refetch();
              void orgs.refetch();
            }}
          />
        </div>
      ) : projects.length === 0 ? (
        <Onboarding canCreate={canCreate} onCreate={() => setDialog(true)} />
      ) : (
        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
          {projects.map((p) => (
            <ProjectCard key={p.id} project={p} />
          ))}
        </div>
      )}
      <NewProjectDialog open={open} onOpenChange={setDialog} orgs={orgList.filter((o) => o.role === "admin" || o.role === "owner")} />
    </>
  );
}

const ROLE_TONE = { owner: "primary", admin: "info", viewer: "neutral" } as const;

function ProjectCard({ project: p }: { project: ProjectOut }) {
  return (
    <Link
      href={`/projects/${p.id}`}
      className="surface group flex min-w-0 flex-col gap-4 p-5 transition-shadow outline-none hover:shadow-[var(--elev-3)] focus-visible:ring-2 focus-visible:ring-ring"
    >
      <div className="flex items-start gap-3">
        <span className="flex size-10 shrink-0 items-center justify-center rounded-xl bg-primary-soft text-[15px] font-semibold text-primary">
          {p.name.slice(0, 1).toUpperCase()}
        </span>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <h2 className="truncate text-[15px] font-semibold tracking-[-0.01em]">{p.name}</h2>
            {p.role && <Chip tone={ROLE_TONE[p.role]}>{PROJECT_ROLE_LABEL[p.role]}</Chip>}
          </div>
          <p className="mt-0.5 line-clamp-2 text-[12.5px] leading-relaxed text-muted-foreground">{p.description || "No description yet."}</p>
        </div>
      </div>
      <div className="mt-auto grid grid-cols-2 gap-2 rounded-xl bg-muted/70 px-4 py-3">
        <Figure label="Connections" value={p.connections ?? 0} />
        <Figure label="Members" value={p.members ?? 0} />
      </div>
      <span className="-mt-1 inline-flex items-center gap-1 self-end text-[12px] font-medium text-primary">
        Open
        <ArrowRightIcon className="size-3 transition-transform group-hover:translate-x-0.5" />
      </span>
    </Link>
  );
}

function Figure({ label, value }: { label: string; value: number }) {
  return (
    <div className="min-w-0">
      <div className="truncate text-[10.5px] font-semibold tracking-[0.04em] text-subtle uppercase">{label}</div>
      <div className={cn("tabular text-[20px] leading-tight font-semibold tracking-[-0.02em]")}>{formatNumber(value)}</div>
    </div>
  );
}

const STEPS = [
  { icon: DatabaseIcon, title: "Connect a database", body: "PostgreSQL, MySQL, SQLite, SQL Server, MongoDB or Redis. The credential is sealed server side; teammates never see it." },
  { icon: ShieldCheckIcon, title: "Read, safely, by default", body: "Every query is guarded to a single read-only statement, and the connection itself is opened read-only wherever the engine allows it." },
  { icon: ScrollTextIcon, title: "Writes, confirmed and audited", body: "A deliberate write is described, confirmed a second time, then recorded in the audit log before it runs." },
];

function Onboarding({ onCreate, canCreate }: { onCreate: () => void; canCreate: boolean }) {
  return (
    <div className="surface overflow-hidden">
      <div className="px-6 pt-8 pb-6 text-center sm:px-10">
        <div className="mx-auto mb-4 flex size-12 items-center justify-center rounded-2xl bg-primary-soft text-primary">
          <WorkflowIcon className="size-6" />
        </div>
        <h2 className="text-[19px] font-semibold tracking-[-0.02em]">Fly your team&apos;s databases from one cockpit</h2>
        <p className="mx-auto mt-2 max-w-xl text-[13.5px] leading-relaxed text-muted-foreground">
          A project holds the connections one team shares, its queries, backups, schema comparisons and audit log. Start with a
          project, then add your first connection.
        </p>
      </div>
      <ol className="grid gap-3 px-6 pb-6 sm:px-10 md:grid-cols-3">
        {STEPS.map((s, i) => (
          <li key={s.title} className="rounded-xl bg-muted/70 p-4">
            <div className="flex items-center gap-2.5">
              <span className="flex size-7 items-center justify-center rounded-lg bg-card text-[12px] font-semibold text-primary shadow-[var(--elev-1)]">{i + 1}</span>
              <s.icon className="size-4 text-subtle" aria-hidden />
            </div>
            <div className="mt-3 text-[13.5px] font-semibold">{s.title}</div>
            <p className="mt-1 text-[12.5px] leading-relaxed text-muted-foreground">{s.body}</p>
          </li>
        ))}
      </ol>
      <div className="flex justify-center bg-muted/40 px-6 py-5 text-center">
        {canCreate ? (
          <Button size="lg" onClick={onCreate}>
            <FolderPlusIcon />
            Create your first project
          </Button>
        ) : (
          <p className="max-w-md text-[13px] text-muted-foreground">You have no projects yet. Ask an organization admin to add you to one.</p>
        )}
      </div>
    </div>
  );
}

function NewProjectDialog({
  open,
  onOpenChange,
  orgs,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  orgs: { id: string; name: string }[];
}) {
  const create = useCreateProject();
  const router = useRouter();
  const [name, setName] = React.useState("");
  const [description, setDescription] = React.useState("");
  const [orgId, setOrgId] = React.useState("");
  const [error, setError] = React.useState<string | null>(null);

  React.useEffect(() => {
    if (open) {
      setName("");
      setDescription("");
      setError(null);
      setOrgId(orgs[0]?.id ?? "");
    }
  }, [open, orgs]);

  function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!orgId) return setError("Choose the organization this project belongs to.");
    if (!name.trim()) return setError("Name the project, for example the team or the data it is about.");
    create.mutate(
      { org_id: orgId, name: name.trim(), description: description.trim() },
      {
        onSuccess: (p) => {
          toast.success(`${p.name} is ready. Add a connection next.`);
          onOpenChange(false);
          router.push(`/projects/${p.id}`);
        },
        onError: (err) => setError(errorMessage(err)),
      },
    );
  }

  return (
    <Dialog open={open} onOpenChange={(o) => !create.isPending && onOpenChange(o)}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>New project</DialogTitle>
          <DialogDescription>You become its owner and can add members afterwards.</DialogDescription>
        </DialogHeader>
        <form onSubmit={submit} className="flex min-h-0 flex-col" noValidate>
          <DialogBody className="space-y-4">
            {orgs.length > 1 && (
              <Field label="Organization">
                <SimpleSelect aria-label="Organization" value={orgId} onValueChange={setOrgId} options={orgs.map((o) => ({ value: o.id, label: o.name }))} />
              </Field>
            )}
            <Field label="Name" htmlFor="np-name">
              <Input id="np-name" autoFocus maxLength={120} placeholder="Platform data" value={name} onChange={(e) => setName(e.target.value)} />
            </Field>
            <Field label="Description" htmlFor="np-desc" hint="Optional. Which data it covers, and who to ask about it.">
              <Textarea id="np-desc" maxLength={2000} value={description} onChange={(e) => setDescription(e.target.value)} />
            </Field>
            {error && <FieldError>{error}</FieldError>}
          </DialogBody>
          <DialogFooter>
            <Button type="button" variant="ghost" onClick={() => onOpenChange(false)}>
              Cancel
            </Button>
            <Button type="submit" disabled={create.isPending}>
              {create.isPending ? "Creating..." : "Create project"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
