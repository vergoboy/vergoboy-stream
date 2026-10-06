// Client-side crash reporting.
//
// A release Tauri build has no devtools, so a JS exception during render was
// previously invisible: the page just went blank or froze with nothing in any
// log. This forwards what the browser knows to the backend's crash log, which is
// the only place it can still be read after the window dies.
//
// Installed from the root layout, so it catches errors from every page.

"use client";

import { useEffect } from "react";
import { apiUrl } from "@/lib/config";

// Keep in sync with MAX_FIELD in app_logging.py; the server truncates too, this
// just avoids shipping a megabyte of stack over the wire.
const MAX_FIELD = 4000;
const MAX_REPORTS_PER_SESSION = 50;

type ClientReport = {
  kind: string;
  message: string;
  source?: string | null;
  line?: number | null;
  column?: number | null;
  url?: string | null;
  stack?: string | null;
};

function trim(value: unknown, max = MAX_FIELD): string {
  const text = typeof value === "string" ? value : String(value ?? "");
  return text.length <= max ? text : `${text.slice(0, max)}...<truncated ${text.length - max}B>`;
}

function describe(value: unknown): string {
  if (value instanceof Error) return `${value.name}: ${value.message}`;
  if (typeof value === "string") return value;
  try {
    return trim(JSON.stringify(value), 1000);
  } catch {
    // Circular object or a BigInt: JSON.stringify throws, which would replace
    // the error we are trying to report with a different one.
    return "[unserialisable value]";
  }
}

/**
 * Send one report to the backend. Fire-and-forget and never throws: a failed
 * report must not become a second error to report.
 */
async function send(report: ClientReport): Promise<void> {
  try {
    await fetch(apiUrl("/stream/api/log/client"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      // `keepalive` so a report issued while the page is unloading still lands.
      keepalive: true,
      body: JSON.stringify(report),
    });
  } catch {
    // Intentionally silent — see above.
  }
}

let reportsSent = 0;

export function reportClientError(report: ClientReport): void {
  if (reportsSent >= MAX_REPORTS_PER_SESSION) return;
  reportsSent += 1;
  void send(report);
}

export default function ClientErrorReporter() {
  useEffect(() => {
    // window.onerror — synchronous errors (a bad render, a thrown handler).
    const onError = (event: ErrorEvent) => {
      reportClientError({
        kind: "window.onerror",
        message: describe(event.error) || event.message || "unknown error",
        source: event.filename ?? null,
        line: event.lineno ?? null,
        column: event.colno ?? null,
        url: event.filename ?? null,
        // Chrome populates `error.stack`; the older fields above are the
        // fallback for engines that do not.
        stack: event.error instanceof Error ? trim(event.error.stack) : null,
      });
    };

    // unhandledrejection — the async equivalent, and the one this app hits
    // most: a failed fetch or a media promise rejecting leaves the UI waiting
    // forever with nothing logged.
    const onRejection = (event: PromiseRejectionEvent) => {
      const reason = event.reason;
      reportClientError({
        kind: "unhandledrejection",
        message: describe(reason),
        stack: reason instanceof Error ? trim(reason.stack) : null,
      });
    };

    window.addEventListener("error", onError);
    window.addEventListener("unhandledrejection", onRejection);
    return () => {
      window.removeEventListener("error", onError);
      window.removeEventListener("unhandledrejection", onRejection);
    };
  }, []);

  return null;
}