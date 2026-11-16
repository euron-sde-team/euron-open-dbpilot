"use client";

import { Trash2Icon, UserPlusIcon } from "lucide-react";
import { useRouter } from "next/navigation";
import * as React from "react";
import { toast } from "sonner";

import { AdminOnly } from "@/components/admin-only";
import { ConfirmDialog } from "@/components/confirm-dialog";
import { PageHeader } from "@/components/page-header";
import { Surface, SurfaceHeader } from "@/components/surface";
import { Button } from "@/components/ui/button";
import { Chip } from "@/components/ui/chip";
import { Input, Textarea } from "@/components/ui/input";
import { Field } from "@/components/ui/label";
import { SimpleSelect } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { errorMessage } from "@/lib/api/errors";
import { formatRelative, initials } from "@/lib/format";
import { PROJECT_ROLE_LABEL, PROJECT_ROLES } from "@/lib/roles";

import { useAddMember, useCurrentProject, useDeleteProject, useMembers, useRemoveMember, useUpdateMember, useUpdateProject } from "../api";

export function SettingsScreen() {
  const { pid, project, role, canManageMembers } = useCurrentProject();

  if (!project) return <Skeleton className="h-96 rounded-[14px]" />;
  if (role !== "owner" && role !== "admin") return <AdminOnly what="Project settings" who="project owners and admins" />;

  return (
    <>
      <PageHeader title="Settings" description="This project's details, members and their roles." />
      <div className="space-y-4">
        <ProjectDetailsCard pid={pid} name={project.name} description={project.description} canEdit={role === "owner"} />
        <MembersCard pid={pid} canManageMembers={canManageMembers} />
      </div>
    </>
  );
}

function ProjectDetailsCard({ pid, name, description, canEdit }: { pid: string; name: string; description: string; canEdit: boolean }) {
  const [editName, setEditName] = React.useState(name);
  const [editDescription, setEditDescription] = React.useState(description);
  const [deleting, setDeleting] = React.useState(false);
  const update = useUpdateProject(pid);
  const del = useDeleteProject(pid);
  const router = useRouter();

  function save(e: React.FormEvent) {
    e.preventDefault();
    update.mutate(
      { name: editName.trim() || null, description: editDescription.trim() },
      {
        onSuccess: () => toast.success("Project updated."),
        onError: (err) => toast.error(errorMessage(err)),
      },
    );
  }

  return (
    <Surface>
      <SurfaceHeader title="Project" />
      <form onSubmit={save} className="space-y-4 px-5 pb-5">
        <Field label="Name" htmlFor="settings-name">
          <Input id="settings-name" maxLength={120} value={editName} onChange={(e) => setEditName(e.target.value)} disabled={!canEdit} />
        </Field>
        <Field label="Description" htmlFor="settings-desc">
          <Textarea id="settings-desc" maxLength={2000} value={editDescription} onChange={(e) => setEditDescription(e.target.value)} disabled={!canEdit} />
        </Field>
        {canEdit && (
          <div className="flex items-center justify-between">
            <Button type="submit" disabled={update.isPending}>
              {update.isPending ? "Saving..." : "Save"}
            </Button>
            <Button type="button" variant="destructive-soft" size="sm" onClick={() => setDeleting(true)}>
              <Trash2Icon />
              Delete project
            </Button>
          </div>
        )}
      </form>
      <ConfirmDialog
        open={deleting}
        onOpenChange={setDeleting}
        title={`Delete ${name}?`}
        description="Every connection, saved query, backup and audit entry in this project is removed. This cannot be undone."
        confirmLabel="Delete project"
        pending={del.isPending}
        onConfirm={() =>
          del.mutate(undefined, {
            onSuccess: () => {
              toast.success(`${name} deleted.`);
              router.push("/projects");
            },
            onError: (err) => toast.error(errorMessage(err)),
          })
        }
      />
    </Surface>
  );
}

function MembersCard({ pid, canManageMembers }: { pid: string; canManageMembers: boolean }) {
  const members = useMembers(pid);
  const addMember = useAddMember(pid);
  const updateMember = useUpdateMember(pid);
  const removeMember = useRemoveMember(pid);
  const [email, setEmail] = React.useState("");
  const [role, setRole] = React.useState<"viewer" | "admin" | "owner">("viewer");
  const [error, setError] = React.useState<string | null>(null);

  function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!email.trim()) return setError("Enter the email of someone already in this organization.");
    setError(null);
    addMember.mutate(
      { email: email.trim(), role },
      {
        onSuccess: () => {
          toast.success("Member added.");
          setEmail("");
        },
        onError: (err) => setError(errorMessage(err)),
      },
    );
  }

  return (
    <Surface>
      <SurfaceHeader title="Members" description={`${members.data?.length ?? 0} in this project.`} />
      {members.isPending ? (
        <div className="px-5 pb-5">
          <Skeleton className="h-32" />
        </div>
      ) : (
        <ul className="divide-y divide-hairline px-5">
          {(members.data ?? []).map((m) => (
            <li key={m.user_id} className="flex items-center gap-3 py-2.5">
              <span className="flex size-8 shrink-0 items-center justify-center rounded-full bg-primary-soft text-[11px] font-semibold text-primary">{initials(m.name)}</span>
              <span className="min-w-0 flex-1">
                <span className="block truncate text-[13px] font-medium">{m.name}</span>
                <span className="block truncate text-[12px] text-subtle">{m.email}, joined {formatRelative(m.joined_at)}</span>
              </span>
              {canManageMembers ? (
                <SimpleSelect
                  aria-label={`Role for ${m.name}`}
                  size="sm"
                  className="w-28"
                  value={m.role}
                  onValueChange={(v) =>
                    updateMember.mutate({ userId: m.user_id, role: v as "viewer" | "admin" | "owner" }, { onError: (err) => toast.error(errorMessage(err)) })
                  }
                  options={PROJECT_ROLES.map((r) => ({ value: r, label: PROJECT_ROLE_LABEL[r] }))}
                />
              ) : (
                <Chip tone="neutral">{PROJECT_ROLE_LABEL[m.role]}</Chip>
              )}
              {canManageMembers && (
                <Button
                  size="icon-sm"
                  variant="subtle"
                  aria-label={`Remove ${m.name}`}
                  onClick={() => removeMember.mutate(m.user_id, { onError: (err) => toast.error(errorMessage(err)) })}
                >
                  <Trash2Icon />
                </Button>
              )}
            </li>
          ))}
        </ul>
      )}
      {canManageMembers && (
        <form onSubmit={submit} className="flex flex-wrap items-end gap-2 px-5 py-4">
          <Field label="Email" htmlFor="member-email" className="min-w-48 flex-1" error={error ?? undefined}>
            <Input id="member-email" type="email" placeholder="teammate@company.com" value={email} onChange={(e) => setEmail(e.target.value)} />
          </Field>
          <Field label="Role">
            <SimpleSelect
              aria-label="Role"
              size="sm"
              className="w-28"
              value={role}
              onValueChange={(v) => setRole(v as "viewer" | "admin" | "owner")}
              options={PROJECT_ROLES.map((r) => ({ value: r, label: PROJECT_ROLE_LABEL[r] }))}
            />
          </Field>
          <Button type="submit" disabled={addMember.isPending}>
            <UserPlusIcon />
            {addMember.isPending ? "Adding..." : "Add"}
          </Button>
        </form>
      )}
    </Surface>
  );
}
