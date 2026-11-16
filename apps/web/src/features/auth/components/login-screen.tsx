"use client";

import { ArrowRightIcon, DatabaseIcon, EyeIcon, EyeOffIcon, ScrollTextIcon, ShieldCheckIcon } from "lucide-react";
import { useRouter, useSearchParams } from "next/navigation";
import * as React from "react";
import { toast } from "sonner";

import { BrandLogo, BrandMark } from "@/components/brand";
import { ThemeToggle } from "@/components/theme-toggle";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { FieldError, Label } from "@/components/ui/label";
import { errorMessage } from "@/lib/api/errors";
import { safeNext } from "@/lib/auth-redirect";
import { cn } from "@/lib/cn";

import { useLogin } from "../api";

const PITCH = [
  { icon: ShieldCheckIcon, title: "Read-only, twice over", body: "The SQL guard accepts only one read-only query, and the database session itself is opened read-only wherever the engine supports it." },
  { icon: DatabaseIcon, title: "Six engines, one cockpit", body: "PostgreSQL, MySQL, SQLite, SQL Server, MongoDB and Redis, discovered from the real connection, never guessed." },
  { icon: ScrollTextIcon, title: "Every write, confirmed and logged", body: "A human-marked write is described in full, confirmed a second time, then recorded in the audit log before it runs." },
];

type Tone = "ok" | "live" | "wait";
const DEMO_STEPS: { step: string; result: string; tone: Tone }[] = [
  { step: "guard    parsed as one SELECT", result: "accepted", tone: "ok" },
  { step: "session  opened read-only", result: "confirmed", tone: "ok" },
  { step: "scan     planner expects 4,200 rows", result: "under limit", tone: "live" },
  { step: "result   112 rows, truncated: false", result: "41 ms", tone: "ok" },
];
const TONE_CLASS: Record<Tone, string> = {
  ok: "bg-[var(--panel-ok-soft)] text-[var(--panel-ok)]",
  live: "bg-[var(--panel-live-soft)] text-[var(--panel-live)]",
  wait: "bg-[var(--panel-wait-soft)] text-[var(--panel-wait)]",
};

export function LoginScreen() {
  const router = useRouter();
  const params = useSearchParams();
  const login = useLogin();
  const [email, setEmail] = React.useState("");
  const [password, setPassword] = React.useState("");
  const [show, setShow] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);

  const expired = params.get("reason") === "expired";
  const next = params.get("next");

  function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!email.trim() || !password) {
      setError("Enter your email and password.");
      return;
    }
    setError(null);
    login.mutate(
      { email: email.trim(), password },
      {
        onSuccess: (session) => {
          toast.success(`Welcome back, ${session.user.name.split(" ")[0]}.`);
          router.replace(safeNext(next));
        },
        onError: (err) => setError(errorMessage(err)),
      },
    );
  }

  return (
    <div className="grid min-h-dvh bg-background lg:grid-cols-[minmax(0,1fr)_minmax(0,1.05fr)]">
      <div className="relative flex min-w-0 flex-col px-5 py-6 sm:px-10">
        <div className="flex items-center justify-between">
          <BrandLogo />
          <ThemeToggle />
        </div>
        <div className="flex flex-1 items-center justify-center py-10">
          <div className="w-full max-w-[360px]">
            <h1 className="text-[26px] leading-tight font-semibold tracking-[-0.025em]">Sign in to DBPilot</h1>
            <p className="mt-1.5 text-[13.5px] leading-relaxed text-muted-foreground">
              A shared cockpit for your team&apos;s databases. Sign in with the account your project owner gave you.
            </p>
            {expired && (
              <div role="status" className="mt-5 rounded-lg bg-warning-soft px-3.5 py-2.5 text-[13px] text-warning">
                Your session expired. Sign in again to pick up where you left off.
              </div>
            )}
            <form onSubmit={submit} className="mt-6 space-y-4" noValidate>
              <div className="space-y-1.5">
                <Label htmlFor="email">Email</Label>
                <Input
                  id="email"
                  type="email"
                  autoComplete="username"
                  autoFocus
                  placeholder="you@company.com"
                  className="h-10"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  aria-invalid={!!error || undefined}
                />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="password">Password</Label>
                <div className="relative">
                  <Input
                    id="password"
                    type={show ? "text" : "password"}
                    autoComplete="current-password"
                    className="h-10 pr-10"
                    value={password}
                    onChange={(e) => setPassword(e.target.value)}
                    aria-invalid={!!error || undefined}
                  />
                  <button
                    type="button"
                    onClick={() => setShow((s) => !s)}
                    aria-label={show ? "Hide password" : "Show password"}
                    className="absolute top-1/2 right-2 flex size-7 -translate-y-1/2 items-center justify-center rounded-md text-subtle hover:text-foreground focus-visible:outline-2 focus-visible:outline-ring"
                  >
                    {show ? <EyeOffIcon className="size-4" /> : <EyeIcon className="size-4" />}
                  </button>
                </div>
              </div>
              {error && <FieldError>{error}</FieldError>}
              <Button type="submit" size="lg" className="w-full" disabled={login.isPending}>
                {login.isPending ? "Signing in..." : "Sign in"}
                {!login.isPending && <ArrowRightIcon />}
              </Button>
            </form>
            <p className="mt-6 text-xs leading-relaxed text-muted-foreground">
              Accounts and project access are managed by your project owner. Forgot your password? Ask them to reset it, or run{" "}
              <code className="rounded bg-muted px-1 py-0.5 font-mono text-[11px]">dbpilot seed</code> for the demo accounts.
            </p>
          </div>
        </div>
        <p className="text-[11.5px] text-faint">Self-hosted. Credentials for every connected database stay sealed on your server.</p>
      </div>

      <BrandPanel />
    </div>
  );
}

function BrandPanel() {
  return (
    <aside className="relative hidden overflow-hidden bg-[var(--panel)] p-10 text-white lg:flex lg:flex-col lg:justify-between">
      <div
        aria-hidden
        className="pointer-events-none absolute inset-0 opacity-90"
        style={{
          background:
            "radial-gradient(60% 50% at 80% 10%, var(--panel-glow), transparent 70%), radial-gradient(50% 45% at 10% 90%, var(--panel-glow-2), transparent 70%)",
        }}
      />
      <div
        aria-hidden
        className="pointer-events-none absolute inset-0 opacity-[0.07]"
        style={{
          backgroundImage: "linear-gradient(white 1px, transparent 1px), linear-gradient(90deg, white 1px, transparent 1px)",
          backgroundSize: "36px 36px",
        }}
      />
      <div className="relative flex items-center gap-2 text-[13px] font-medium text-white/70">
        <BrandMark className="size-6" />
        The open-source team database cockpit
      </div>

      <div className="relative max-w-lg">
        <h2 className="text-[34px] leading-[1.12] font-semibold tracking-[-0.03em]">
          Fly your team&apos;s databases
          <br />
          <span className="text-[var(--panel-accent)]">from one shared cockpit.</span>
        </h2>
        <p className="mt-3 text-[14.5px] leading-relaxed text-white/65">
          Nobody holds the raw password. Every read is guarded and run on a read-only session; every write is shown before it runs,
          confirmed a second time, and logged.
        </p>

        <div className="mt-8 rounded-2xl bg-white/[0.06] p-4 shadow-[0_20px_60px_rgba(0,0,0,0.35)] ring-1 ring-white/10 backdrop-blur" aria-label="Example query">
          <div className="flex items-center justify-between gap-3 text-[12px] text-white/55">
            <span className="text-white/80">SELECT * FROM orders WHERE status = &apos;shipped&apos;</span>
            <span className="rounded-full bg-[var(--panel-accent-soft)] px-2 py-0.5 font-semibold text-[var(--panel-accent)]">Guard passed</span>
          </div>
          <ul className="mt-3 space-y-1.5 font-mono text-[12px]">
            {DEMO_STEPS.map((r) => (
              <li key={r.step} className="flex items-center justify-between gap-3 rounded-lg bg-white/[0.04] px-3 py-1.5">
                <span className="truncate whitespace-pre text-white/75">{r.step}</span>
                <span className={cn("shrink-0 rounded-full px-2 py-0.5 font-semibold", TONE_CLASS[r.tone])}>{r.result}</span>
              </li>
            ))}
          </ul>
        </div>

        <ul className="mt-8 grid gap-5">
          {PITCH.map((p) => (
            <li key={p.title} className="flex gap-3">
              <span className="flex size-8 shrink-0 items-center justify-center rounded-lg bg-white/10 text-[var(--panel-accent)]">
                <p.icon className="size-4" />
              </span>
              <div>
                <div className="text-[13px] font-semibold">{p.title}</div>
                <div className="mt-0.5 text-[12.5px] leading-relaxed text-white/55">{p.body}</div>
              </div>
            </li>
          ))}
        </ul>
      </div>

      <div className="relative text-[11.5px] text-white/40">MIT licensed. Part of the Euron open-source initiative.</div>
    </aside>
  );
}
