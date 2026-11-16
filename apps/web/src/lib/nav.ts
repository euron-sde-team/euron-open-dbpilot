import {
  ActivityIcon,
  ArchiveIcon,
  DatabaseIcon,
  GaugeIcon,
  GitCompareIcon,
  ScrollTextIcon,
  SettingsIcon,
  SquareTerminalIcon,
  type LucideIcon,
} from "lucide-react";

export type Audience = "project" | "project-admin" | "project-owner";

export interface NavItem {
  /** A path; inside a project, relative to /projects/<id>. */
  href: string;
  label: string;
  icon: LucideIcon;
  audience: Audience;
}

export interface NavGroup {
  label: string;
  items: NavItem[];
}

export const NAV: NavGroup[] = [
  {
    label: "Cockpit",
    items: [{ href: "", label: "Overview", icon: GaugeIcon, audience: "project" }],
  },
  {
    label: "Data",
    items: [
      { href: "/connections", label: "Connections", icon: DatabaseIcon, audience: "project" },
      { href: "/editor", label: "SQL editor", icon: SquareTerminalIcon, audience: "project" },
      { href: "/comparisons", label: "Schema compare", icon: GitCompareIcon, audience: "project" },
      { href: "/backups", label: "Backups", icon: ArchiveIcon, audience: "project" },
    ],
  },
  {
    label: "Govern",
    items: [
      { href: "/audit", label: "Audit log", icon: ScrollTextIcon, audience: "project" },
      { href: "/settings", label: "Settings", icon: SettingsIcon, audience: "project-owner" },
    ],
  },
];

export const ADMIN_NAV: NavItem[] = [{ href: "/admin/system", label: "System", icon: ActivityIcon, audience: "project" }];

/** The project id in a path like /projects/<id>/editor, or null. */
export function projectIdFrom(pathname: string): string | null {
  const m = /^\/projects\/([0-9a-f-]{36})(?:\/|$)/.exec(pathname);
  return m?.[1] ?? null;
}

/** The real href of an item: project items are prefixed with the current project. */
export function hrefOf(item: NavItem, projectId: string | null): string | null {
  if (item.href.startsWith("/admin")) return item.href;
  return projectId ? `/projects/${projectId}${item.href}` : null;
}

/** Whether someone sees an item, given their project role. */
export function visibleTo(item: NavItem, projectId: string | null, projectRole?: string | null): boolean {
  if (item.href.startsWith("/admin")) return true;
  if (!projectId) return false;
  if (item.audience === "project-owner") return projectRole === "owner";
  if (item.audience === "project-admin") return projectRole === "admin" || projectRole === "owner";
  return true;
}

/** The nav item a path belongs to: the longest matching prefix. */
export function activeHref(pathname: string, projectId: string | null): string | null {
  const path = pathname.replace(/\/+$/, "") || "/";
  const hrefs = [...NAV.flatMap((g) => g.items), ...ADMIN_NAV]
    .map((i) => hrefOf(i, projectId))
    .filter((h): h is string => !!h);
  return hrefs.filter((h) => path === h || path.startsWith(`${h}/`)).sort((a, b) => b.length - a.length)[0] ?? null;
}
