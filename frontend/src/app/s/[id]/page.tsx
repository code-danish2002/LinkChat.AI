import type { Metadata } from "next";

import { SourceWorkspace } from "@/components/sources/SourceWorkspace";

export const metadata: Metadata = { title: "Chat" };

export default async function SourcePage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <SourceWorkspace sourceId={id} />;
}
