"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import type { Socket } from "socket.io-client";
import { API_ORIGIN, SOCKET_PATH } from "./config";
import type {
  ChatMessage,
  NotifyEvent,
  PresenceUser,
  RoomStateSync,
  TranscodeProgress,
} from "./types";

const EMPTY_ROOM: RoomStateSync = {
  playlist: [],
  current_index: null,
  playing: false,
  position: 0,
  rate: 1,
  server_time: Date.now() / 1000,
  online: 0,
};

export interface ControlExtra {
  at?: number;
  to?: number;
  rate?: number;
  index?: number;
}

interface UseRoomStateOptions {
  myName: string;
  onNotify?: (n: NotifyEvent) => void;
}

export function useRoomState({ myName, onNotify }: UseRoomStateOptions) {
  const [connected, setConnected] = useState(false);
  const [room, setRoom] = useState<RoomStateSync>(EMPTY_ROOM);
  const [presenceUsers, setPresenceUsers] = useState<PresenceUser[]>([]);
  const [notifications, setNotifications] = useState<NotifyEvent[]>([]);
  const [chatMessages, setChatMessages] = useState<ChatMessage[]>([]);
  const [transcodeProgress, setTranscodeProgress] = useState<Record<string, TranscodeProgress>>({});

  const socketRef = useRef<Socket | null>(null);
  const recvLocalRef = useRef<number>(0);
  const onNotifyRef = useRef(onNotify);
  useEffect(() => {
    onNotifyRef.current = onNotify;
  }, [onNotify]);

  useEffect(() => {
    recvLocalRef.current = Date.now() / 1000;
  }, []);

  useEffect(() => {
    if (!myName || socketRef.current) return;
    let cancelled = false;

    import("socket.io-client").then(({ io }) => {
      if (cancelled) return;
      const socket = io(API_ORIGIN || undefined, {
        path: SOCKET_PATH,
        transports: ["websocket"],
      });
      socketRef.current = socket;

      socket.on("connect", () => {
        setConnected(true);
        socket.emit("join", { name: myName });
      });
      socket.on("disconnect", () => setConnected(false));

      socket.on("state_sync", (data: RoomStateSync) => {
        recvLocalRef.current = Date.now() / 1000;
        setRoom(data);
      });

      socket.on("presence", (data: { online: number; users: PresenceUser[] }) => {
        setPresenceUsers(data.users || []);
        setRoom((r) => ({ ...r, online: data.online || 0 }));
      });

      socket.on("notify", (n: NotifyEvent) => {
        setNotifications((prev) => [n, ...prev].slice(0, 40));
        onNotifyRef.current?.(n);
      });

      socket.on("transcode_progress", (data: TranscodeProgress) => {
        setTranscodeProgress((prev) => ({ ...prev, [`${data.id}:${data.label}`]: data }));
      });

      socket.on("chat_history", (data: { messages: ChatMessage[] }) => {
        setChatMessages(data.messages || []);
      });
      socket.on("chat_message", (msg: ChatMessage) => {
        setChatMessages((prev) => [...prev, msg]);
      });
      socket.on("chat_cleared", () => setChatMessages([]));
      socket.on("chat_purged", () => {
        import("./api").then(({ api }) => api.chatHistory()).then((d) => setChatMessages(d.messages || []));
      });
    });

    return () => {
      cancelled = true;
      socketRef.current?.disconnect();
      socketRef.current = null;
    };
  }, [myName]);

  const expectedPosition = useCallback(() => {
    const elapsed = room.playing ? (Date.now() / 1000 - recvLocalRef.current) * room.rate : 0;
    return room.position + elapsed;
  }, [room.playing, room.position, room.rate]);

  const requestControl = useCallback(
    (action: "play" | "pause" | "seek" | "rate" | "select", extra: ControlExtra = {}) => {
      // Optimistic local update so the UI feels instant.
      setRoom((r) => {
        const next = { ...r };
        const now = Date.now() / 1000;
        switch (action) {
          case "play":
            next.playing = true;
            next.position = extra.at ?? r.position;
            recvLocalRef.current = now;
            break;
          case "pause":
            next.playing = false;
            next.position = extra.at ?? r.position;
            recvLocalRef.current = now;
            break;
          case "seek":
            next.position = extra.to ?? r.position;
            recvLocalRef.current = now;
            break;
          case "rate":
            next.position = expectedPosition();
            next.rate = extra.rate ?? r.rate;
            recvLocalRef.current = now;
            break;
          case "select":
            next.current_index = extra.index ?? null;
            next.position = 0;
            next.playing = false;
            recvLocalRef.current = now;
            break;
        }
        return next;
      });
      socketRef.current?.emit("control", { action, name: myName, ...extra });
    },
    [expectedPosition, myName]
  );

  const sendChat = useCallback(
    (text: string, imageUrl: string | null) => {
      socketRef.current?.emit("chat_send", { name: myName, text, image_url: imageUrl });
    },
    [myName]
  );

  const setVoiceActive = useCallback(
    (active: boolean) => {
      socketRef.current?.emit(active ? "voice_joined" : "voice_left", { name: myName });
    },
    [myName]
  );

  return {
    connected,
    room,
    presenceUsers,
    notifications,
    chatMessages,
    transcodeProgress,
    expectedPosition,
    requestControl,
    sendChat,
    setVoiceActive,
  };
}
