"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Room, RoomEvent, Track, ConnectionState, ConnectionQuality, supportsAudioOutputSelection } from "livekit-client";
import type { LocalAudioTrack, Participant } from "livekit-client";
import { api } from "./api";
import { useAppSettings } from "./settings";
import type { VoiceParticipant, VoiceSettings, VoiceQuality } from "./types";

function parseMeta(p: Participant): { name: string; avatarUrl: string | null } {
  let name = p.name || p.identity;
  let avatarUrl: string | null = null;
  try {
    if (p.metadata) {
      const m = JSON.parse(p.metadata);
      if (typeof m.name === "string" && m.name) name = m.name;
      if (typeof m.avatar_url === "string" && m.avatar_url) avatarUrl = m.avatar_url;
    }
  } catch {
    /* ignore bad metadata */
  }
  return { name, avatarUrl };
}

// Client-side voice DSP chain (browser WebRTC), mirroring the recommended path:
//   Mic -> AEC (echoCancellation) -> AGC (autoGainControl)
//        -> NS (noiseSuppression) -> Opus encode -> SFU
// Receive: Opus -> decode -> per-user volume -> limiter/clipping guard.
// (VAD for the speaking indicator runs server-side on the LiveKit SFU.)
const QUALITY_PRESETS: Record<VoiceQuality, { sampleRate?: number; channelCount?: number }> = {
  low: { sampleRate: 16000, channelCount: 1 },
  medium: { sampleRate: 48000, channelCount: 1 },
  high: { sampleRate: 48000, channelCount: 2 },
  // "auto" (the default) is mono so the mic never defaults to stereo.
  auto: { channelCount: 1 },
};

function captureOptions(s: VoiceSettings, deviceId?: string) {
  return {
    deviceId: deviceId || undefined,
    echoCancellation: s.echoCancellation,
    noiseSuppression: s.noiseSuppression,
    autoGainControl: s.autoGainControl,
    ...(QUALITY_PRESETS[s.quality] ?? {}),
  };
}

/**
 * The honest voice connection lifecycle. Every one of these is derived from a
 * real LiveKit event or a real browser signal — nothing is ever optimistically
 * reported as connected.
 *
 * - `disconnected`  not in the voice room (the only resting state)
 * - `connecting`    token request / WebSocket handshake in flight
 * - `connected`     media transport established, audio actually flowing
 * - `reconnecting`  the transport dropped and LiveKit is retrying; the user is
 *                   still "in" the room, so the UI must say so rather than
 *                   pretending a clean disconnect
 * - `failed`        the attempt gave up; `error` carries the real reason
 *
 * Microphone problems are a SEPARATE axis on purpose: a blocked or denied
 * microphone does not disconnect you from the room, and conflating the two used
 * to make a working connection look broken (or a broken one look fine).
 */
export type VoiceStatus = "disconnected" | "connecting" | "connected" | "reconnecting" | "failed";

/** Why the microphone is not producing audio, when it isn't. */
export type MicStatus = "ready" | "muted" | "blocked" | "denied" | "nodevice";

export interface VoiceDiagnostics {
  /** LiveKit signaling websocket actually in use, e.g. ws://127.0.0.1:7880 */
  signalUrl: string | null;
  participantIdentity: string | null;
  localParticipantCount: number;
  remoteParticipantCount: number;
  /** Live audio track actually attached locally (null while muted/absent). */
  micTrackSid: string | null;
  audioInputDevices: number;
  audioOutputDevices: number;
  lastError: string | null;
  lastErrorAt: number | null;
  /** True only when we have seen a real Connected event. */
  everConnected: boolean;
  reconnectAttempts: number;
}

export interface UseVoiceRoomResult {
  /** @deprecated prefer `status === "connected"` — this stays for compatibility. */
  connected: boolean;
  connecting: boolean;
  status: VoiceStatus;
  micStatus: MicStatus;
  diagnostics: VoiceDiagnostics;
  error: string | null;
  audioBlocked: boolean;
  micMuted: boolean;
  deafened: boolean;
  talking: boolean;
  participants: VoiceParticipant[];
  speakingIdentities: Set<string>;
  networkQuality: string;
  devices: MediaDeviceInfo[];
  selectedDevice: string;
  outputDevices: MediaDeviceInfo[];
  selectedOutputDevice: string;
  settings: VoiceSettings;
  setSettings: (s: VoiceSettings) => void;
  join: () => Promise<void>;
  leave: () => Promise<void>;
  toggleMic: () => void;
  toggleDeafen: () => void;
  setTalking: (on: boolean) => void;
  setParticipantVolume: (identity: string, volume: number) => void;
  setMasterVolume: (volume: number) => void;
  changeDevice: (deviceId: string) => Promise<void>;
  changeOutputDevice: (deviceId: string) => Promise<void>;
  unlockAudio: () => Promise<void>;
  refreshDevices: () => Promise<void>;
}

function isTypingTarget(e: Event): boolean {
  const t = e.target as HTMLElement | null;
  const tag = t?.tagName?.toLowerCase();
  const type = (t as HTMLInputElement | null)?.type?.toLowerCase();
  if (tag === "textarea" || tag === "select") return true;
  if (tag === "input" && type !== "range") return true;
  return Boolean(t?.isContentEditable);
}

export function useVoiceRoom(myName: string, myAvatarUrl: string | null): UseVoiceRoomResult {
  const { settings: appSettings, setVoice } = useAppSettings();
  const settings = appSettings.voice;
  const roomRef = useRef<Room | null>(null);
  const micRef = useRef<LocalAudioTrack | null>(null);
  const volumesRef = useRef<Record<string, number>>({});
  const microphoneUpdateRef = useRef<Promise<void>>(Promise.resolve());
  const appliedCaptureSettingsRef = useRef<string | null>(null);
  const connectedOnceRef = useRef(false);

  const [connected, setConnected] = useState(false);
  const [connecting, setConnecting] = useState(false);
  const [status, setStatus] = useState<VoiceStatus>("disconnected");
  const [micStatus, setMicStatus] = useState<MicStatus>("muted");
  const [diagnostics, setDiagnostics] = useState<VoiceDiagnostics>({
    signalUrl: null,
    participantIdentity: null,
    localParticipantCount: 0,
    remoteParticipantCount: 0,
    micTrackSid: null,
    audioInputDevices: 0,
    audioOutputDevices: 0,
    lastError: null,
    lastErrorAt: null,
    everConnected: false,
    reconnectAttempts: 0,
  });
  const [error, setError] = useState<string | null>(null);
  const [micMuted, setMicMuted] = useState(false);
  const [deafened, setDeafened] = useState(false);
  const deafenedRef = useRef(deafened);
  useEffect(() => {
    deafenedRef.current = deafened;
  }, [deafened]);
  const [participants, setParticipants] = useState<VoiceParticipant[]>([]);
  const [speakingIdentities, setSpeakingIdentities] = useState<Set<string>>(new Set());
  const [networkQuality, setNetworkQuality] = useState<ConnectionQuality>(ConnectionQuality.Unknown);
  const [devices, setDevices] = useState<MediaDeviceInfo[]>([]);
  const [selectedDevice, setSelectedDevice] = useState("");
  const [outputDevices, setOutputDevices] = useState<MediaDeviceInfo[]>([]);
  const [selectedOutputDevice, setSelectedOutputDevice] = useState("");
  const [audioBlocked, setAudioBlocked] = useState(false);
  const gestureCleanupRef = useRef<(() => void) | null>(null);
  const [talking, setTalkingRaw] = useState(false);
  const talkingRef = useRef(false);
  const pushToTalkRef = useRef(appSettings.pushToTalk);
  useEffect(() => {
    pushToTalkRef.current = appSettings.pushToTalk;
  }, [appSettings.pushToTalk]);

  const settingsRef = useRef(settings);
  useEffect(() => {
    settingsRef.current = settings;
  }, [settings]);

  const buildParticipants = useCallback((room: Room): VoiceParticipant[] => {
    const local = room.localParticipant;
    const list: VoiceParticipant[] = [];
    const seen = new Set<string>();
    const add = (p: Participant) => {
      if (seen.has(p.identity)) return;
      seen.add(p.identity);
      const { name, avatarUrl } = parseMeta(p);
      const micPub = p.getTrackPublication(Track.Source.Microphone);
      const muted = micPub ? micPub.isMuted : true;
      const v = volumesRef.current[p.identity];
      list.push({
        identity: p.identity,
        name,
        avatarUrl,
        isSpeaking: p.isSpeaking,
        muted,
        audioLevel: p.audioLevel || 0,
        volume: typeof v === "number" ? v : 1,
        isLocal: p.identity === local.identity,
      });
    };
    add(local);
    for (const p of room.remoteParticipants.values()) add(p);
    list.sort((a, b) => (a.isLocal ? -1 : b.isLocal ? 1 : a.name.localeCompare(b.name)));
    return list;
  }, []);

  const refreshParticipants = useCallback(
    (room: Room) => setParticipants(buildParticipants(room)),
    [buildParticipants]
  );

  // Effective volume for a remote participant = per-user volume x master,
  // clamped to zero while deafened. This is the single source of truth used by
  // every path (subscribe, per-user slider, master slider, deafen toggle).
  const applyVolume = useCallback((room: Room, identity: string) => {
    const raw = volumesRef.current[identity] ?? 1;
    const target = deafenedRef.current ? 0 : raw * settingsRef.current.masterVolume;
    room.remoteParticipants.get(identity)?.setVolume(target, Track.Source.Microphone);
  }, []);

  const setVolumeOf = useCallback(
    (room: Room, identity: string, volume: number) => {
      volumesRef.current[identity] = volume;
      applyVolume(room, identity);
      setParticipants((prev) => prev.map((x) => (x.identity === identity ? { ...x, volume } : x)));
    },
    [applyVolume]
  );

  const refreshDevices = useCallback(async () => {
    try {
      const list = await Room.getLocalDevices("audioinput");
      setDevices(list);
      const found = list.some((d) => d.deviceId === selectedDevice);
      setSelectedDevice(found ? selectedDevice : (list[0]?.deviceId ?? ""));
      setDiagnostics((d) => ({ ...d, audioInputDevices: list.length }));
      // No input device at all is a distinct, reportable condition.
      if (list.length === 0) setMicStatus((s) => (s === "ready" ? s : "nodevice"));
    } catch {
      /* permission not granted yet — devices refresh again after joining */
    }
    try {
      const outs = await Room.getLocalDevices("audiooutput");
      setOutputDevices(outs);
      const wanted = settingsRef.current.outputDevice;
      const foundOut = outs.some((d) => d.deviceId === wanted);
      setSelectedOutputDevice(foundOut ? wanted : "");
      setDiagnostics((d) => ({ ...d, audioOutputDevices: outs.length }));
    } catch {
      /* some browsers cannot enumerate output devices */
    }
  }, [selectedDevice]);

  const applySettings = useCallback(() => {
    const update = microphoneUpdateRef.current.catch(() => {}).then(async () => {
      const room = roomRef.current;
      if (!room || room.state !== ConnectionState.Connected) return;
      const s = settingsRef.current;
      const cap = captureOptions(s, selectedDevice);
      const captureKey = JSON.stringify(cap);
      if (appliedCaptureSettingsRef.current === captureKey) return;
      try {
        const oldPub = room.localParticipant.getTrackPublication(Track.Source.Microphone);
        if (oldPub?.track) {
          await room.localParticipant.unpublishTrack(oldPub.track);
        }
        micRef.current = null;
        setMicMuted(false);
        const track = await room.localParticipant.setMicrophoneEnabled(true, cap);
        micRef.current = (track?.track as LocalAudioTrack | undefined) ?? null;
        if (pushToTalkRef.current) {
          await room.localParticipant.setMicrophoneEnabled(false).catch(() => {});
          setMicMuted(true);
          setMicStatus("muted");
        } else {
          setMicStatus(micRef.current ? "ready" : "nodevice");
        }
        setDiagnostics((d) => ({ ...d, micTrackSid: micRef.current?.sid ?? null }));
        appliedCaptureSettingsRef.current = captureKey;
      } catch (e) {
        // A microphone failure is NOT a room failure: you can stay connected and
        // hear everyone. Classify the real reason so the UI can say
        // "microphone blocked" instead of a generic connection error.
        const err = e as { name?: string; message?: string };
        const name = err?.name ?? "";
        const denied = name === "NotAllowedError" || name === "PermissionDeniedError" || /permission|denied|notallowed/i.test(err?.message ?? "");
        const nodevice = name === "NotFoundError" || name === "DevicesNotFoundError" || /not found|no device/i.test(err?.message ?? "");
        console.warn("[voice] could not (re)create microphone:", e);
        setMicStatus(denied ? "denied" : nodevice ? "nodevice" : "blocked");
        setDiagnostics((d) => ({
          ...d,
          micTrackSid: null,
          lastError: err?.message ?? String(e),
          lastErrorAt: Date.now() / 1000,
        }));
      }
    });
    microphoneUpdateRef.current = update;
    return update;
  }, [selectedDevice]);

  const applySettingsRef = useRef(applySettings);
  useEffect(() => {
    applySettingsRef.current = applySettings;
  }, [applySettings]);

  // Audio playback must be unlocked by a user gesture (autoplay policies).
  // startAudio() plays attached remote elements and resumes the Web Audio
  // context; call it after connect AND again once remote tracks attach, and
  // retry on any pointer/key press in case the gesture window expired.
  const unlockAudio = useCallback(async () => {
    const room = roomRef.current;
    if (!room) return;
    try {
      await room.startAudio();
      setAudioBlocked(false);
    } catch {
      setAudioBlocked(true);
    }
  }, []);

  // Re-create the mic track whenever a capture setting changes while connected.
  useEffect(() => {
    if (connected) {
      applySettingsRef.current().catch(() => {});
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [settings.quality, settings.echoCancellation, settings.noiseSuppression, settings.autoGainControl, selectedDevice]);

  // Audio-level polling for smooth mic bars (isSpeaking already comes from the
  // SFU's VAD; the wire only carries coarse levels, so we sample locally).
  useEffect(() => {
    if (!connected) return;
    const t = setInterval(() => {
      const room = roomRef.current;
      if (!room) return;
      setParticipants((prev) => {
        let changed = false;
        const next = prev.map((x) => {
          const p = x.isLocal ? room.localParticipant : room.remoteParticipants.get(x.identity);
          const lvl = p?.audioLevel || 0;
          if (Math.abs(lvl - x.audioLevel) < 0.005) return x;
          changed = true;
          return { ...x, audioLevel: lvl };
        });
        return changed ? next : prev;
      });
    }, 180);
    return () => clearInterval(t);
  }, [connected]);

  const leave = useCallback(async () => {
    const room = roomRef.current;
    if (!room) return;
    try {
      await room.disconnect();
    } catch {
      /* already disconnected */
    }
    micRef.current = null;
    roomRef.current = null;
    appliedCaptureSettingsRef.current = null;
    gestureCleanupRef.current?.();
    gestureCleanupRef.current = null;
    talkingRef.current = false;
    setTalkingRaw(false);
    setConnected(false);
    setStatus("disconnected");
    setMicStatus("muted");
    setParticipants([]);
    setSpeakingIdentities(new Set());
    setMicMuted(false);
    setDeafened(false);
    setNetworkQuality(ConnectionQuality.Unknown);
    setDiagnostics((d) => ({
      ...d,
      signalUrl: null,
      participantIdentity: null,
      localParticipantCount: 0,
      remoteParticipantCount: 0,
      micTrackSid: null,
      reconnectAttempts: 0,
    }));
  }, []);

  const join = useCallback(async () => {
    if (roomRef.current) return;
    setError(null);
    setConnecting(true);
    setStatus("connecting");
    try {
      const { url, token } = await api.voiceToken(myName, myAvatarUrl);
      const room = new Room({
        adaptiveStream: true,
        dynacast: false,
        webAudioMix: true,
        audioCaptureDefaults: captureOptions(settingsRef.current),
      });
      roomRef.current = room;
      setDiagnostics((d) => ({
        ...d,
        signalUrl: url || null,
        lastError: null,
      }));
      appliedCaptureSettingsRef.current = null;

      room
        .on(RoomEvent.ConnectionStateChanged, (s) => {
          setConnected(s === ConnectionState.Connected);
          // Map LiveKit's real transport state onto our honest status. The old
          // code only tracked a boolean, so a dropped-and-retrying transport
          // was indistinguishable from a clean disconnect — the UI showed
          // "connected" while nothing was flowing, or claimed a failure the
          // SFU was about to recover from.
          setStatus(
            s === ConnectionState.Connected
              ? "connected"
              : s === ConnectionState.Reconnecting || s === ConnectionState.SignalReconnecting
                ? "reconnecting"
                : s === ConnectionState.Connecting
                  ? "connecting"
                  : "disconnected"
          );
          if (s === ConnectionState.Connected) {
            connectedOnceRef.current = true;
            setDiagnostics((d) => ({ ...d, everConnected: true, reconnectAttempts: 0 }));
          }
        })
        .on(RoomEvent.Reconnecting, () => {
          setStatus("reconnecting");
          setDiagnostics((d) => ({ ...d, reconnectAttempts: d.reconnectAttempts + 1 }));
        })
        .on(RoomEvent.Reconnected, () => {
          setStatus("connected");
          setConnected(true);
          setDiagnostics((d) => ({ ...d, everConnected: true, reconnectAttempts: 0 }));
          refreshParticipants(room);
        })
        .on(RoomEvent.ParticipantConnected, () => refreshParticipants(room))
        .on(RoomEvent.ParticipantDisconnected, () => refreshParticipants(room))
        .on(RoomEvent.ActiveSpeakersChanged, (speakers) => {
          const spk = new Set(speakers.map((s) => s.identity));
          setSpeakingIdentities(spk);
          setParticipants((prev) =>
            prev.map((x) => (x.isSpeaking === spk.has(x.identity) ? x : { ...x, isSpeaking: spk.has(x.identity) }))
          );
        })
        .on(RoomEvent.TrackSubscribed, (track, _pub, p) => {
          if (track.kind === Track.Kind.Audio) {
            const el = track.attach();
            // webAudioMix is enabled, so remote audio is routed through the
            // WebAudio graph; the attached element is deliberately kept muted
            // to avoid double playback (livekit only mutes it when the
            // AudioContext already exists at attach time).
            if (el && typeof el.muted === "boolean") el.muted = true;
          }
          applyVolume(room, p.identity);
          refreshParticipants(room);
          unlockAudio();
        })
        .on(RoomEvent.TrackUnsubscribed, (track) => {
          track?.detach();
          refreshParticipants(room);
        })
        .on(RoomEvent.TrackMuted, () => refreshParticipants(room))
        .on(RoomEvent.TrackUnmuted, () => refreshParticipants(room))
        .on(RoomEvent.LocalTrackPublished, () => refreshParticipants(room))
        .on(RoomEvent.LocalTrackUnpublished, () => refreshParticipants(room))
        .on(RoomEvent.ConnectionQualityChanged, (q, p) => {
          if (p.isLocal) setNetworkQuality(q);
        });

      await room.connect(url, token);

      // Autoplay: connect() ran from the user's click so audio is allowed;
      // startAudio() surfaces any policy failure explicitly. We also listen
      // for any subsequent gesture to retry in case the activation expired.
      const onGesture = () => {
        unlockAudio();
      };
      window.addEventListener("pointerdown", onGesture);
      window.addEventListener("keydown", onGesture);
      gestureCleanupRef.current = () => {
        window.removeEventListener("pointerdown", onGesture);
        window.removeEventListener("keydown", onGesture);
      };
      unlockAudio();

      // Restore persisted per-user volumes after a reconnect.
      for (const identity of Object.keys(volumesRef.current)) {
        applyVolume(room, identity);
      }
      await applySettings();
      const outDevice = settingsRef.current.outputDevice;
      if (outDevice && supportsAudioOutputSelection()) {
        try {
          await room.switchActiveDevice("audiooutput", outDevice);
        } catch (e) {
          console.warn("[voice] could not set audio output:", e);
        }
      }
refreshParticipants(room);
      await refreshDevices();
      // Only claim "connected" if LiveKit actually reports it. `connect()`
      // resolving is necessary but not sufficient evidence of audio.
      setStatus((prev) => (prev === "connected" || prev === "reconnecting" ? prev : room.state === ConnectionState.Connected ? "connected" : prev));
      setDiagnostics((d) => ({
        ...d,
        participantIdentity: room.localParticipant.identity ?? null,
        localParticipantCount: room.localParticipant ? 1 : 0,
        remoteParticipantCount: room.remoteParticipants.size,
      }));
    } catch (e) {
      const msg = e instanceof Error ? e.message : "اتصال به اتاق صوتی ناموفق بود";
      setError(msg);
      // A real failure is a failure — never leave the status at "connecting"
      // or "connected" after the attempt has already given up.
      setStatus("failed");
      setDiagnostics((d) => ({ ...d, lastError: msg, lastErrorAt: Date.now() / 1000 }));
      roomRef.current?.disconnect().catch(() => {});
      roomRef.current = null;
    } finally {
      setConnecting(false);
    }
  }, [myName, myAvatarUrl, applySettings, applyVolume, refreshParticipants, refreshDevices, unlockAudio]);

  const changeDevice = useCallback(
    async (deviceId: string) => {
      setSelectedDevice(deviceId);
      const room = roomRef.current;
      if (!room || room.state !== ConnectionState.Connected) return;
      try {
        await room.switchActiveDevice("audioinput", deviceId);
        await applySettings();
      } catch (e) {
        console.warn("[voice] device switch failed:", e);
      }
    },
    [applySettings]
  );

  const changeOutputDevice = useCallback(
    async (deviceId: string) => {
      setSelectedOutputDevice(deviceId);
      setVoice((s) => ({ ...s, outputDevice: deviceId }));
      const room = roomRef.current;
      if (!room || room.state !== ConnectionState.Connected) return;
      try {
        await room.switchActiveDevice("audiooutput", deviceId);
      } catch (e) {
        console.warn("[voice] output device switch failed:", e);
      }
    },
    [setVoice]
  );

  const toggleMic = useCallback(() => {
    const room = roomRef.current;
    if (!room || room.state !== ConnectionState.Connected) return;
    const next = !room.localParticipant.isMicrophoneEnabled;
    // Reflect the user's intent immediately, then reconcile with the real
    // track state once LiveKit confirms it — a mic that silently failed to
    // enable must not be shown as "ready".
    setMicMuted(!next);
    setMicStatus(next ? "ready" : "muted");
    room.localParticipant
      .setMicrophoneEnabled(next)
      .then(() => {
        const pub = room.localParticipant.getTrackPublication(Track.Source.Microphone);
        const sid = pub?.track?.sid ?? null;
        setDiagnostics((d) => ({ ...d, micTrackSid: sid }));
        if (next && !sid) setMicStatus("blocked");
      })
      .catch((e: unknown) => {
        const err = e as { name?: string; message?: string };
        const denied = err?.name === "NotAllowedError" || err?.name === "PermissionDeniedError";
        setMicStatus(denied ? "denied" : "blocked");
        setDiagnostics((d) => ({ ...d, lastError: err?.message ?? String(e), lastErrorAt: Date.now() / 1000 }));
      });
    refreshParticipants(room);
  }, [refreshParticipants]);

  const toggleDeafen = useCallback(() => {
    setDeafened((d) => {
      const next = !d;
      deafenedRef.current = next;
      const room = roomRef.current;
      if (room) {
        for (const p of room.remoteParticipants.values()) {
          applyVolume(room, p.identity);
        }
      }
      return next;
    });
  }, [applyVolume]);

  // Push-to-talk: while `on` the local mic is published, otherwise muted.
  const setTalking = useCallback(
    (on: boolean) => {
      talkingRef.current = on;
      setTalkingRaw(on);
      const room = roomRef.current;
      if (!room || room.state !== ConnectionState.Connected) return;
      room.localParticipant.setMicrophoneEnabled(on).catch(() => {});
      setMicMuted(!on);
      setMicStatus(on ? "ready" : "muted");
      refreshParticipants(room);
    },
    [refreshParticipants]
  );

  // Enforce the selected voice mode whenever the room (or mode) changes:
  // PTT keeps the mic muted until the key is held; always-on unmutes it.
  useEffect(() => {
    if (!connected) {
      connectedOnceRef.current = false;
      return;
    }
    const shouldTalk = !appSettings.pushToTalk;
    if (!connectedOnceRef.current) {
      connectedOnceRef.current = true;
      talkingRef.current = shouldTalk;
      setTalkingRaw(shouldTalk);
      return;
    }
    setTalking(shouldTalk);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [connected, appSettings.pushToTalk]);

  // Global voice keys: push-to-talk (hold), mic toggle, deafen toggle.
  useEffect(() => {
    if (!connected) return;
    const pttKey = appSettings.shortcuts.pushToTalk;
    const micKey = appSettings.shortcuts.toggleMic;
    const deafenKey = appSettings.shortcuts.toggleDeafen;
    const isPtt = appSettings.pushToTalk;

    const onDown = (e: KeyboardEvent) => {
      if (e.repeat || isTypingTarget(e)) return;
      if (isPtt && e.key === pttKey) setTalking(true);
      else if (e.key === micKey) toggleMic();
      else if (e.key === deafenKey) toggleDeafen();
    };
    const onUp = (e: KeyboardEvent) => {
      if (isPtt && e.key === pttKey) setTalking(false);
    };
    const onBlur = () => {
      if (isPtt) setTalking(false);
    };
    window.addEventListener("keydown", onDown);
    window.addEventListener("keyup", onUp);
    window.addEventListener("blur", onBlur);
    return () => {
      window.removeEventListener("keydown", onDown);
      window.removeEventListener("keyup", onUp);
      window.removeEventListener("blur", onBlur);
    };
  }, [connected, appSettings.pushToTalk, appSettings.shortcuts.pushToTalk, appSettings.shortcuts.toggleMic, appSettings.shortcuts.toggleDeafen, setTalking, toggleMic, toggleDeafen]);

  const setParticipantVolume = useCallback((identity: string, volume: number) => {
    const room = roomRef.current;
    if (room) setVolumeOf(room, identity, volume);
  }, [setVolumeOf]);

  const setMasterVolume = useCallback(
    (volume: number) => {
      setVoice((s) => ({ ...s, masterVolume: volume }));
      const room = roomRef.current;
      if (!room) return;
      for (const p of room.remoteParticipants.values()) {
        applyVolume(room, p.identity);
      }
    },
    [setVoice, applyVolume]
  );

  const setSettings = useCallback((s: VoiceSettings) => setVoice(s), [setVoice]);

  // Auto-quality: pick a capture preset from the SFU's connection-quality
  // report (only while "auto" is enabled in the panel).
  useEffect(() => {
    if (!connected || !settings.autoQuality || settings.quality !== "auto") return;
    let target: VoiceQuality = "auto";
    switch (networkQuality) {
      case ConnectionQuality.Excellent:
      case ConnectionQuality.Good:
        target = "high";
        break;
      case ConnectionQuality.Poor:
        target = "medium";
        break;
      default:
        return;
    }
    const s = settingsRef.current;
    if (s.quality !== target) setVoice((prev) => ({ ...prev, quality: target }));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [networkQuality, connected, settings.autoQuality, settings.quality]);

  // Cleanup on unmount.
  useEffect(() => {
    return () => {
      gestureCleanupRef.current?.();
      gestureCleanupRef.current = null;
      roomRef.current?.disconnect().catch(() => {});
      roomRef.current = null;
    };
  }, []);

  return {
    connected,
    connecting,
    status,
    micStatus,
    diagnostics,
    error,
    audioBlocked,
    micMuted,
    deafened,
    talking,
    participants,
    speakingIdentities,
    networkQuality,
    devices,
    selectedDevice,
    outputDevices,
    selectedOutputDevice,
    settings,
    setSettings,
    join,
    leave,
    toggleMic,
    toggleDeafen,
    setTalking,
    setParticipantVolume,
    setMasterVolume,
    changeDevice,
    changeOutputDevice,
    unlockAudio,
    refreshDevices,
  };
}
