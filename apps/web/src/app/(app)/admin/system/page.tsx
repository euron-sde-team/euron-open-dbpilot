import type { Metadata } from "next";
import { Suspense } from "react";

import { SystemScreen } from "@/features/admin/components/system-screen";

export const metadata: Metadata = { title: "System" };

export default function Page() {
  return (
    <Suspense>
      <SystemScreen />
    </Suspense>
  );
}
