import { cn } from "@/lib/cn";

/** The DBPilot mark: a control yoke over a cylinder, the shared cockpit flying a database. */
export function BrandMark({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 44 44" aria-hidden className={cn("size-7", className)}>
      <rect width="44" height="44" rx="12" className="fill-primary" />
      <g className="stroke-primary-foreground" strokeWidth="2.4" fill="none" strokeLinecap="round">
        <ellipse cx="22" cy="14.5" rx="10.5" ry="4.2" />
        <path d="M11.5 14.5v15c0 2.3 4.7 4.2 10.5 4.2s10.5-1.9 10.5-4.2v-15" />
        <path d="M15 22.5h14" />
        <path d="M22 22.5v7.5" />
      </g>
    </svg>
  );
}

export function BrandLogo({ collapsed = false, className }: { collapsed?: boolean; className?: string }) {
  return (
    <span className={cn("inline-flex items-center gap-2", className)}>
      <BrandMark />
      {!collapsed && <span className="text-[15px] font-semibold tracking-[-0.02em]">DBPilot</span>}
    </span>
  );
}
