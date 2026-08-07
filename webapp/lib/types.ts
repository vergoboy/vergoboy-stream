export type ItemStatus = "queued" | "encoding" | "ready" | "complete" | "error";
export type RenditionStatus = "pending" | "queued" | "encoding" | "ready" | "complete" | "error";

export interface Rendition {
  label: string;
  height: number;
  vbr: string;
  abr: string;
  status: RenditionStatus | undefined;
  is_default?: boolean;
  error?: string;
}

export interface Subtitle {
  id: string;
  label: string;
  lang: string;
  url: string;
}

export interface AudioTrack {
  id: string;
  label: string;
  url: string;
}

export type ItemType = "file" | "url" | "youtube" | "live";

export interface PlaylistItem {
  id: string;
  type: ItemType;
  title: string;
  src: string | null;
  subtitles: Subtitle[];
  audio_tracks: AudioTrack[];
  added_by: string;
  added_at: number;
  status: ItemStatus;
  error?: string;
  renditions?: Rendition[];
  yt_url?: string;
  key?: string;
}

export interface RoomStateSync {
  playlist: PlaylistItem[];
  current_index: number | null;
  playing: boolean;
  position: number;
  rate: number;
  server_time: number;
  online: number;
}

export interface PresenceUser {
  name: string;
  avatar_url: string | null;
  in_voice?: boolean;
}

export interface NotifyEvent {
  type: string;
  name: string;
  ts: number;
  title?: string;
  label?: string;
  id?: string;
  count?: number;
  extra?: Record<string, unknown>;
  [key: string]: unknown;
}

export interface TranscodeProgress {
  id: string;
  label: string;
  pct: number;
  encoded_seconds: number | null;
  duration: number | null;
  ready: boolean;
  complete?: boolean;
}

export interface ChatMessage {
  id: string;
  name: string;
  text: string;
  image_url: string | null;
  avatar_url: string | null;
  ts: number;
}

export interface SubStyle {
  font: "Vazirmatn" | "JetBrainsMono" | "Tahoma" | "system-ui";
  size: number;
  color: string;
  bg: string;
  bgOpacity: number;
  outline: boolean;
  bold: boolean;
  offset: number;
}

export const DEFAULT_SUB_STYLE: SubStyle = {
  font: "Vazirmatn",
  size: 28,
  color: "#ffffff",
  bg: "#000000",
  bgOpacity: 60,
  outline: true,
  bold: false,
  offset: 40,
};

export type VoiceQuality = "auto" | "high" | "medium" | "low";

export interface VoiceSettings {
  quality: VoiceQuality;
  echoCancellation: boolean;
  noiseSuppression: boolean;
  autoGainControl: boolean;
  autoQuality: boolean;
  masterVolume: number;
  outputDevice: string;
}

export const DEFAULT_VOICE_SETTINGS: VoiceSettings = {
  quality: "auto",
  echoCancellation: true,
  noiseSuppression: true,
  autoGainControl: true,
  autoQuality: true,
  masterVolume: 1,
  outputDevice: "",
};

export interface VoiceParticipant {
  identity: string;
  name: string;
  avatarUrl: string | null;
  isSpeaking: boolean;
  muted: boolean;
  audioLevel: number;
  volume: number;
  isLocal: boolean;
}
