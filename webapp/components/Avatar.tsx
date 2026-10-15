"use client";

import { useState } from "react";

import { nameHueColor, nameInitial } from "@/lib/format";
import { mediaUrl } from "@/lib/config";

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
  const [failedSrc, setFailedSrc] = useState<string | null>(null);
  const src = url ? mediaUrl(url) : null;
  const broken = src != null && failedSrc === src;
  if (src && !broken) {
    return (
      // eslint-disable-next-line @next/next/no-img-element -- dynamic, remote-origin avatar; next/image gives no benefit since images.unoptimized is set
      <img
        src={src}
        alt={name}
        width={size}
        height={size}
        onError={() => setFailedSrc(src)}
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