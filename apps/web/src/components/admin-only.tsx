import { ShieldIcon } from "lucide-react";
import Link from "next/link";

import { EmptyState } from "@/components/empty-state";
import { Surface } from "@/components/surface";
import { Button } from "@/components/ui/button";

/** Shown to people whose project role does not open this page. */
export function AdminOnly({ what, who = "project owners" }: { what: string; who?: string }) {
  return (
    <Surface>
      <EmptyState
        icon={ShieldIcon}
        title="Not for this role"
        description={`${what} is for ${who}. Ask a project owner if you need access.`}
        action={
          <Button variant="outline" size="sm" render={<Link href="/projects" />}>
            Back to your projects
          </Button>
        }
      />
    </Surface>
  );
}
