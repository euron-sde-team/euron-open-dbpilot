import type { Metadata } from "next";
import { Suspense } from "react";

import { ConnectionDetailScreen } from "@/features/connections/components/connection-detail-screen";

export const metadata: Metadata = { title: "Connection" };

export default async function Page({ params }: { params: Promise<{ cid: string }> }) {
  const { cid } = await params;
  return (
    <Suspense>
      <ConnectionDetailScreen cid={cid} />
    </Suspense>
  );
}
