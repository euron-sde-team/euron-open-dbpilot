import type { Metadata } from "next";
import { Suspense } from "react";

import { SettingsScreen } from "@/features/projects/components/settings-screen";

export const metadata: Metadata = { title: "Settings" };

export default function Page() {
  return (
    <Suspense>
      <SettingsScreen />
    </Suspense>
  );
}
