"use client";

import { useEffect, useRef, useState, useCallback } from "react";
import type { State } from "./sync";
import { Client as SyncClient } from "./sync";

export type SyncRole = "controller" | "watcher";

export interface SyncInfo {
  url: string;
  token: string;
  room: string;
  role: SyncRole;
  expires_at: number;
}

export function useRoomSync(opts: { roomCode: string; myName: string }) {
  const [syncInfo, setSyncInfo] = useState<SyncInfo | null>(null);
  const [connected, setConnected] = useState(false);
  const [state, setState] = useState<State | null>(null);
  const wsRef = useRef<WebSocket | null>(null);
  const clientRef = useRef<SyncClient | null>(null);
  const pingTimer = useRef<number | null>(null);
  const driftTimer = useRef<number | null>(null);
  const clockOffset = useRef(0); // server_now ≈ local_now + offset (approx)

  // Fetch sync token
  useEffect(() => {
    let dead = false;
    (async () => {
      try {
        const res = await fetch("/api/room/sync-token", { method: "POST" });
        if (!res.ok) return;
        const data = await res.json();
        if (!dead) setSyncInfo(data);
      } catch {}
    })();
    return () => { dead = true; };
  }, [opts.roomCode]);

  // Connect WS
  useEffect(() => {
    if (!syncInfo) return;
    const ws = new WebSocket(syncInfo.url);
    wsRef.current = ws;
    clientRef.current = new SyncClient("Room");

    ws.onopen = () => {
      setConnected(true);
      clientRef.current!.on_connected();
      ws.send(JSON.stringify({ t: "hello", room: syncInfo.room, token: syncInfo.token }));
      // start ping every 5s
      if (pingTimer.current) window.clearInterval(pingTimer.current);
      pingTimer.current = window.setInterval(() => {
        if (ws.readyState === 1) {
          ws.send(JSON.stringify({ t: "ping", t0: performance.now() / 1000 }));
        }
      }, 5000);
    };

    ws.onclose = () => {
      setConnected(false);
      if (pingTimer.current) { window.clearInterval(pingTimer.current); pingTimer.current = null; }
      // retry in 2s
      setTimeout(() => {
        // trigger reconnect by forcing token refresh? simple: just let effect re-run if info same? reload info
      }, 2000);
    };

    ws.onmessage = (ev) => {
      try {
        const m = JSON.parse(ev.data);
        if (m.t === "state") {
          const st: State = {
            seq: m.seq, playing: m.playing, waiting: m.waiting,
            anchor_pos: m.anchor_pos, anchor_server_time: m.anchor_server_time,
            rate: m.rate, item: m.item,
          };
          clientRef.current!.on_state(st);
          setState(st);
        } else if (m.t === "pong") {
          // crude offset: server_now ≈ m.ts; local_now ≈ m.t0 + rtt/2? we don't have t1 precise; use m.ts - m.t0 approx? keep simple
          if (m.ts != null) clockOffset.current = m.ts - (performance.now()/1000); // rough
        }
      } catch {}
    };

    return () => {
      if (pingTimer.current) { window.clearInterval(pingTimer.current); pingTimer.current = null; }
      ws.close();
      wsRef.current = null;
      clientRef.current = null;
    };
  }, [syncInfo]);

  function nowServer(): number {
    return performance.now() / 1000 + clockOffset.current;
  }

  const sendCmd = useCallback((cmd: unknown): void => {
    if (wsRef.current?.readyState !== 1) return;
    const c = clientRef.current;
    if (!c || !syncInfo) return;
    const out = c.emit(syncInfo.role, cmd);
    if (!out) return;
    const o = out as any;
    if (o.kind === "play") wsRef.current.send(JSON.stringify({ t: "cmd", c: "play" }));
    else if (o.kind === "pause") wsRef.current.send(JSON.stringify({ t: "cmd", c: "pause" }));
    else if (o.kind === "seek") wsRef.current.send(JSON.stringify({ t: "cmd", c: "seek", target: o.target }));
    else if (o.kind === "rate") wsRef.current.send(JSON.stringify({ t: "cmd", c: "rate", rate: o.rate }));
    else if (o.kind === "item") wsRef.current.send(JSON.stringify({ t: "cmd", c: "item", item: o.item }));
    else wsRef.current.send(JSON.stringify({ t: "cmd", ...o }));
  }, [syncInfo]);

  const expected = useCallback((localPos: number, nowLocal: number) => {
    const c = clientRef.current;
    if (!c || !c.room) return localPos;
    return c.expected(nowServer());
  }, [nowServer]);

  return {
    syncInfo,
    connected,
    state,
    client: clientRef,
    nowServer,
    sendCmd,
    expected,
  };
}
