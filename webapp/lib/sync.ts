" use client";
type Secs = number;

export const DRIFT_IGNORE = 0.3;
export const DRIFT_SEEK = 2.0;
export const SEEK_COOLDOWN = 3.0;
export const OWN_SEEK_TOLERANCE = 0.5;
export const PAUSE_AT_MAX_DISTANCE = 5.0;

export type State = {
  seq: number;
  playing: boolean;
  waiting: boolean;
  anchor_pos: Secs;
  anchor_server_time: Secs;
  rate: number;
  item: string;
};

export type Phase = "Connecting" | "Syncing" | "Ready" | "Buffering";
export type Mode = "Room" | "Local";
export type Drift =
  | { kind: "Hold" }
  | { kind: "Ok" }
  | { kind: "Rate"; rate: number }
  | { kind: "Seek"; target: Secs };
export type SeekKind = "Echo" | "Seeked" | "UserSeek";

export class Client {
  phase: Phase = "Connecting";
  mode: Mode;
  room: State | null = null;
  last_seq = 0;
  applying_remote = false;
  own_seek_target: Secs | null = null;
  last_seek_at: Secs | null = null;

  constructor(mode: Mode = "Room") {
    this.mode = mode;
  }

  now(): Secs { return performance.now() / 1000; }

  expected(now_server: Secs): Secs {
    if (!this.room) return 0;
    if (this.room.playing) {
      return Math.max(0, this.room.anchor_pos + (now_server - this.room.anchor_server_time) * this.room.rate);
    }
    return this.room.anchor_pos;
  }

  on_state(st: State): boolean {
    if (this.room != null && st.seq < this.last_seq) return false;
    if (this.room != null && st.seq == this.last_seq) return true;
    this.last_seq = st.seq;
    this.last_seek_at = null;
    this.room = { ...st };
    if (this.phase === "Connecting" || this.phase === "Syncing") {
      this.phase = "Ready";
    }
    return true;
  }

  on_reconnect(): void {
    this.phase = "Syncing";
    this.room = null;
    this.last_seq = 0;
    this.own_seek_target = null;
  }

  on_connected(): void {
    if (this.phase === "Connecting") this.phase = "Syncing";
  }

  set_buffering(b: boolean): void {
    if (this.phase === "Ready" && b) { this.phase = "Buffering"; return; }
    if (this.phase === "Buffering" && !b) { this.phase = "Ready"; return; }
  }

  drift_action(local_pos: Secs, now_server: Secs, now_local: Secs): Drift {
    if (!this.room) return { kind: "Hold" };
    if (this.phase !== "Ready") return { kind: "Hold" };
    const exp = this.expected(now_server);
    const d = local_pos - exp;
    const cooled = this.last_seek_at == null ? true : (now_local - this.last_seek_at) >= SEEK_COOLDOWN;
    const ad = Math.abs(d);
    if (!this.room.playing) {
      if (ad > DRIFT_IGNORE && cooled) return this.do_seek(this.room.anchor_pos, now_local);
      return { kind: "Ok" };
    }
    if (ad < DRIFT_IGNORE) return { kind: "Ok" };
    if (ad <= DRIFT_SEEK) {
      const f = 0.03 + 0.02 * ((ad - DRIFT_IGNORE) / (DRIFT_SEEK - DRIFT_IGNORE));
      const r = d > 0.0 ? this.room.rate * (1.0 - f) : this.room.rate * (1.0 + f);
      return { kind: "Rate", rate: r };
    }
    if (cooled) return this.do_seek(exp, now_local);
    return { kind: "Ok" };
  }

  do_seek(target: Secs, now_local: Secs): Drift {
    this.own_seek_target = Math.max(0, target);
    this.last_seek_at = now_local;
    this.applying_remote = true;
    return { kind: "Seek", target: this.own_seek_target };
  }

  on_player_seek(target: Secs): SeekKind {
    if (this.own_seek_target != null) {
      if (Math.abs(this.own_seek_target - target) <= OWN_SEEK_TOLERANCE) {
        this.own_seek_target = null;
        this.applying_remote = false;
        return "Seeked";
      }
    }
    if (this.applying_remote) return "Echo";
    return "UserSeek";
  }

  emit(role: "controller" | "watcher", cmd: unknown): unknown | null {
    if (this.mode === "Local") return null;
    if (role !== "controller") return null;
    if (this.applying_remote) return null;
    if (this.phase === "Connecting" || this.phase === "Syncing") return null;
    return cmd;
  }
}
