import type { Metadata } from "next";
import { Suspense } from "react";

import { ComparisonsScreen } from "@/features/comparisons/components/comparisons-screen";

export const metadata: Metadata = { title: "Schema compare" };

export default function Page() {
  return (
    <Suspense>
      <ComparisonsScreen />
    </Suspense>
  );
}
