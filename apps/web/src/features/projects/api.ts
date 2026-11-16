"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useParams } from "next/navigation";

import { api, unwrap } from "@/lib/api/client";
import type { MemberOut, ProjectOut, ProjectRole } from "@/lib/api/types";
import { qk, STALE } from "@/lib/query-keys";
import { projectPermissions, type ProjectPermissions } from "@/lib/roles";

const p = (pid: string) => ({ params: { path: { project_id: pid } } });

/** The project id from the URL (/projects/[pid]/...). */
export function useProjectId(): string {
  const params = useParams<{ pid: string }>();
  return params.pid;
}

export function useProjects() {
  return useQuery({ queryKey: qk.projects, queryFn: () => unwrap(api.GET("/api/v1/projects")), staleTime: STALE.list });
}

export function useProject(pid: string, enabled = true) {
  return useQuery({
    queryKey: qk.project(pid),
    queryFn: () => unwrap(api.GET("/api/v1/projects/{project_id}", p(pid))),
    staleTime: STALE.config,
    enabled: enabled && !!pid,
  });
}

/**
 * The current project and what the signed-in person may do in it. While the project is
 * loading every permission is false, so no edit control flashes for a viewer.
 */
export function useCurrentProject(): {
  pid: string;
  project: ProjectOut | undefined;
  role: ProjectRole | undefined;
  query: ReturnType<typeof useProject>;
} & ProjectPermissions {
  const pid = useProjectId();
  const query = useProject(pid);
  const role = query.data?.role;
  return { pid, project: query.data, role, query, ...projectPermissions(role) };
}

export function useCreateProject() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body: { org_id: string; name: string; description?: string }) => unwrap(api.POST("/api/v1/projects", { body })),
    onSuccess: () => client.invalidateQueries({ queryKey: qk.projects }),
  });
}

export function useUpdateProject(pid: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body: { name?: string | null; description?: string | null }) => unwrap(api.PATCH("/api/v1/projects/{project_id}", { ...p(pid), body })),
    onSuccess: (data) => {
      client.setQueryData(qk.project(pid), data);
      void client.invalidateQueries({ queryKey: qk.projects });
    },
  });
}

export function useDeleteProject(pid: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: () => unwrap(api.DELETE("/api/v1/projects/{project_id}", p(pid))),
    onSuccess: () => {
      client.removeQueries({ queryKey: qk.project(pid) });
      void client.invalidateQueries({ queryKey: qk.projects });
    },
  });
}

export function useProjectOverview(pid: string) {
  return useQuery({
    queryKey: qk.overview(pid),
    queryFn: () => unwrap(api.GET("/api/v1/projects/{project_id}/overview", p(pid))),
    staleTime: STALE.live,
    enabled: !!pid,
  });
}

export function useMembers(pid: string) {
  return useQuery<MemberOut[]>({
    queryKey: qk.members(pid),
    queryFn: () => unwrap(api.GET("/api/v1/projects/{project_id}/members", p(pid))),
    staleTime: STALE.list,
    enabled: !!pid,
  });
}

export function useAddMember(pid: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body: { email: string; role: ProjectRole }) => unwrap(api.POST("/api/v1/projects/{project_id}/members", { ...p(pid), body })),
    onSuccess: () => client.invalidateQueries({ queryKey: qk.members(pid) }),
  });
}

export function useUpdateMember(pid: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ userId, role }: { userId: string; role: ProjectRole }) =>
      unwrap(api.PATCH("/api/v1/projects/{project_id}/members/{user_id}", { params: { path: { project_id: pid, user_id: userId } }, body: { role } })),
    onSuccess: () => client.invalidateQueries({ queryKey: qk.members(pid) }),
  });
}

export function useRemoveMember(pid: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (userId: string) => unwrap(api.DELETE("/api/v1/projects/{project_id}/members/{user_id}", { params: { path: { project_id: pid, user_id: userId } } })),
    onSuccess: () => client.invalidateQueries({ queryKey: qk.members(pid) }),
  });
}
