"use client";

// Minimal WHIP (WebRTC-HTTP Ingestion Protocol) publisher for MediaMTX.
// The browser captures the screen/window/tab and pushes it straight to the
// server over WebRTC; MediaMTX remuxes it to HLS so every viewer watches it
// through the normal synced player — no OBS needed.

export type WhipState = "connecting" | "connected" | "failed" | "closed";

export interface WhipPublishOptions {
  onState?: (state: WhipState, detail?: string) => void;
  maxBitrate?: number;
}

function waitIceGathering(pc: RTCPeerConnection, timeoutMs = 2500): Promise<void> {
  if (pc.iceGatheringState === "complete") return Promise.resolve();
  return new Promise((resolve) => {
    const done = () => {
      clearTimeout(timer);
      pc.removeEventListener("icegatheringstatechange", onChange);
      resolve();
    };
    const onChange = () => {
      if (pc.iceGatheringState === "complete") done();
    };
    const timer = setTimeout(done, timeoutMs);
    pc.addEventListener("icegatheringstatechange", onChange);
  });
}

// MediaMTX remuxes WebRTC to HLS, and its HLS muxer only accepts H264 video —
// so we pin the video codec to H264 (every Chromium/Safari capture supports it).
function preferH264(pc: RTCPeerConnection) {
  const caps = typeof RTCRtpSender.getCapabilities === "function" ? RTCRtpSender.getCapabilities("video") : null;
  if (!caps?.codecs?.length) return;
  const h264 = caps.codecs.filter((c) => c.mimeType.toLowerCase() === "video/h264");
  if (!h264.length) throw new Error("مرورگر شما از کدک H264 پشتیبانی نمی‌کند؛ از کروم استفاده کن");
  const rest = caps.codecs.filter((c) => c.mimeType.toLowerCase() !== "video/h264");
  for (const t of pc.getTransceivers()) {
    if (t.receiver.track?.kind === "video" || t.sender.track?.kind === "video") {
      try {
        t.setCodecPreferences([...h264, ...rest]);
      } catch {
        /* older browsers — negotiation order stays as-is */
      }
    }
  }
}

export async function publishWhip(stream: MediaStream, url: string, opts: WhipPublishOptions = {}): Promise<RTCPeerConnection> {
  const pc = new RTCPeerConnection();
  opts.onState?.("connecting");

  pc.onconnectionstatechange = () => {
    switch (pc.connectionState) {
      case "connected":
        opts.onState?.("connected");
        break;
      case "failed":
        opts.onState?.("failed", "اتصال شبکه برقرار نشد");
        break;
      case "disconnected":
      case "closed":
        opts.onState?.("closed");
        break;
    }
  };

  for (const track of stream.getTracks()) {
    if (track.kind === "video") {
      track.contentHint = "motion";
    }
    pc.addTrack(track, stream);
  }

  preferH264(pc);

  const videoSender = pc.getSenders().find((s) => s.track?.kind === "video");
  if (videoSender) {
    try {
      const p = videoSender.getParameters();
      if (!p.encodings?.length) p.encodings = [{}];
      p.encodings[0].maxBitrate = opts.maxBitrate ?? 3_000_000;
      // keep framerate smooth (movies/games) instead of sharpening static text
      (p as RTCRtpSendParameters & { degradationPreference?: string }).degradationPreference = "maintain-framerate";
      await videoSender.setParameters(p);
    } catch {
      /* not critical */
    }
  }

  await pc.setLocalDescription(await pc.createOffer());
  await waitIceGathering(pc);

  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/sdp" },
    body: pc.localDescription!.sdp,
  });
  if (!res.ok) {
    const detail = (await res.text().catch(() => "")).slice(0, 200);
    pc.close();
    throw new Error(`WHIP ${res.status}${detail ? `: ${detail}` : ""}`);
  }
  const answer = await res.text();
  await pc.setRemoteDescription({ type: "answer", sdp: answer });

  return pc;
}
