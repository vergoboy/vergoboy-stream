"use client";

import { useEffect, useRef } from "react";
import { animate, stagger, prefersReducedMotion } from "@/lib/anim";

/**
 * The room itself: back wall, velvet curtains, valance, projector beam and a
 * few specks of dust floating in the beam. Purely decorative, pointer-events
 * none, and a sibling of the player (never its ancestor).
 */
export function Scene({ still }: { still?: boolean }) {
  const dust = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const host = dust.current;
    if (!host || still || prefersReducedMotion()) return;
    const specks = Array.from(host.children) as HTMLElement[];
    const anims = specks.map((el, i) => {
      const x = 8 + ((i * 53) % 84);
      el.style.left = `${x}%`;
      el.style.top = `${10 + ((i * 37) % 80)}%`;
      return animate(el, {
        translateX: [0, (i % 2 ? 1 : -1) * (14 + (i % 4) * 6)],
        translateY: [0, -(24 + (i % 5) * 10)],
        opacity: [0.05, 0.5, 0.05],
        duration: 7000 + (i % 5) * 1500,
        delay: i * 380,
        loop: true,
        ease: "inOutSine",
      });
    });
    return () => anims.forEach((a) => a.cancel());
  }, [still]);

  return (
    <div aria-hidden className="pointer-events-none absolute inset-0">
      <div className="scene-layer scene-wall" />
      <div className="scene-layer room-dim">
        <div className="scene-beam" />
        <div ref={dust} className="absolute left-1/2 top-[12%] h-[70%] w-[min(60vw,720px)] -translate-x-1/2">
          {Array.from({ length: 14 }).map((_, i) => (
            <span key={i} className="absolute h-[3px] w-[3px] rounded-full bg-[color:var(--color-amber-soft)] opacity-0" />
          ))}
        </div>
      </div>
      <div className="room-dim">
        <div className="scene-curtain is-left" />
        <div className="scene-curtain is-right" />
        <div className="scene-valance" />
      </div>
    </div>
  );
}

/** Marquee sign above the screen. Bulbs run a wave once on arrival; it replays when someone new arrives. */
export function Marquee({ pulseKey, compact }: { pulseKey: string | number; compact?: boolean }) {
  const row = useRef<HTMLDivElement>(null);
  const first = useRef(true);

  useEffect(() => {
    const bulbs = row.current?.querySelectorAll(".bulb");
    if (!bulbs?.length) return;
    if (prefersReducedMotion()) return;
    animate(bulbs, {
      opacity: [0.15, 1, 0.75],
      scale: [0.6, 1.5, 1],
      delay: stagger(26, { from: first.current ? "first" : "center" }),
      duration: 700,
      ease: "outQuad",
    });
    first.current = false;
  }, [pulseKey]);

  return (
    <div className="room-dim flex justify-center">
      <div
        className="relative flex items-center gap-3 rounded-xl border border-[color:var(--color-amber)]/40 bg-[#1a0b10]/80 px-4 py-1.5 shadow-[0_0_30px_-6px_rgba(247,195,90,0.5)]"
        style={{ transform: "translateZ(0)" }}
      >
        <span className="display text-[17px] leading-none text-[color:var(--color-amber)] sm:text-[22px]">سینمای ما</span>
        {!compact && (
          <div ref={row} className="flex items-center gap-[7px]" aria-hidden>
            {Array.from({ length: 14 }).map((_, i) => (
              <span key={i} className="bulb" style={{ opacity: 0.75 }} />
            ))}
          </div>
        )}
        {compact && (
          <div ref={row} className="flex items-center gap-[6px]" aria-hidden>
            {Array.from({ length: 6 }).map((_, i) => (
              <span key={i} className="bulb" style={{ opacity: 0.75 }} />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
