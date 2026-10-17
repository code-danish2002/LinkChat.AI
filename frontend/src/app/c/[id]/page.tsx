import type { Metadata } from "next";

import { CollectionWorkspace } from "@/components/collections/CollectionWorkspace";

export const metadata: Metadata = { title: "Collection" };

export default async function CollectionPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <CollectionWorkspace collectionId={id} />;
}
