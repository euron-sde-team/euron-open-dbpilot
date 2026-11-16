import type { Metadata } from "next";
import { Suspense } from "react";

import { BackupsScreen } from "@/features/backups/components/backups-screen";

export const metadata: Metadata = { title: "Backups" };

export default function Page() {
  return (
    <Suspense>
      <BackupsScreen />
    </Suspense>
  );
}
