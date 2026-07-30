"use client";

import { nameHueColor, nameInitial } from "@/lib/format";

export function Avatar({
  name,
  url,
  size = 36,
  className = "",
}: {
  name: string;
  url?: string | null;
  size?: number;
  className?: string;
}) {
  if (url) {
    return (
      // eslint-disable-next-line @next/next/no-img-element -- dynamic, remote-origin avatar; next/image gives no benefit since images.unoptimized is set
      <img
        src={url}
        alt={name}
        width={size}
        height={size}
        className={`rounded-full object-cover shrink-0 ${className}`}
        style={{ width: size, height: size }}
      />
    );
  }
  return (
    <div
      className={`rounded-full shrink-0 flex items-center justify-center font-bold text-white ${className}`}
      style={{ width: size, height: size, background: nameHueColor(name), fontSize: size * 0.42 }}
    >
      {nameInitial(name)}
    </div>
  );
}