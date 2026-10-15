"use client";

import dynamic from "next/dynamic";

const EscapeApp = dynamic(() => import("@/components/EscapeApp").then((mod) => mod.EscapeApp), {
  ssr: false,
  loading: () => <div className="boot">Floodline</div>,
});

export default function Page() {
  return <EscapeApp />;
}
