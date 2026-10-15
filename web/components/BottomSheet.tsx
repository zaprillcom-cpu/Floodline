"use client";

import { useEffect, useRef, useState, type PointerEvent, type ReactNode } from "react";

const SNAPS = [0.2, 0.48, 0.86];

type Props = {
  snap: number;
  onSnap: (index: number) => void;
  children: ReactNode;
};

export function BottomSheet({ snap, onSnap, children }: Props) {
  const sheetRef = useRef<HTMLDivElement>(null);
  const drag = useRef<{ y: number; height: number } | null>(null);
  const [height, setHeight] = useState<number | null>(null);
  const [avail, setAvail] = useState(640);

  useEffect(() => {
    const measure = () => {
      const tab = 64 + (Number.parseFloat(getComputedStyle(document.documentElement).getPropertyValue("env(safe-area-inset-bottom)")) || 0);
      setAvail(Math.max(320, window.innerHeight - tab));
    };
    measure();
    window.addEventListener("resize", measure);
    return () => window.removeEventListener("resize", measure);
  }, []);

  const resolved = height ?? Math.round(avail * SNAPS[Math.min(SNAPS.length - 1, Math.max(0, snap))]);

  function onPointerDown(event: PointerEvent<HTMLButtonElement>) {
    event.currentTarget.setPointerCapture(event.pointerId);
    drag.current = { y: event.clientY, height: sheetRef.current?.getBoundingClientRect().height ?? resolved };
  }

  function onPointerMove(event: PointerEvent<HTMLButtonElement>) {
    if (!drag.current) return;
    const next = drag.current.height + (drag.current.y - event.clientY);
    setHeight(Math.max(96, Math.min(avail * 0.94, next)));
  }

  function finish(event: PointerEvent<HTMLButtonElement>) {
    if (!drag.current) return;
    drag.current = null;
    const current = sheetRef.current?.getBoundingClientRect().height ?? resolved;
    const ratio = current / avail;
    let best = 0;
    let bestDistance = Infinity;
    SNAPS.forEach((value, index) => {
      const distance = Math.abs(value - ratio);
      if (distance < bestDistance) {
        bestDistance = distance;
        best = index;
      }
    });
    setHeight(null);
    onSnap(best);
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
  }

  return (
    <section
      ref={sheetRef}
      className="sheet"
      style={{ height: resolved }}
      aria-label="Floodline"
    >
      <button
        type="button"
        className="handle-hit"
        data-testid="sheet-handle"
        aria-label="Drag the sheet"
        style={{ touchAction: "none" }}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={finish}
        onPointerCancel={finish}
      >
        <span className="handle" />
      </button>
      <div className="sheet-body">{children}</div>
    </section>
  );
}
