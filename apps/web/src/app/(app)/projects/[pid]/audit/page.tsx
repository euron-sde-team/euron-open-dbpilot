import type { Metadata } from "next";
import { Suspense } from "react";

import { AuditScreen } from "@/features/audit/components/audit-screen";

export const metadata: Metadata = { title: "Audit log" };

export default function Page() {
  return (
    <Suspense>
      <AuditScreen />
    </Suspense>
  );
}
