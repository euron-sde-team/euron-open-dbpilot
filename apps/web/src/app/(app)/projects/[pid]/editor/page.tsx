import type { Metadata } from "next";
import { Suspense } from "react";

import { EditorScreen } from "@/features/queries/components/editor-screen";

export const metadata: Metadata = { title: "SQL editor" };

export default function Page() {
  return (
    <Suspense>
      <EditorScreen />
    </Suspense>
  );
}
