(() => {
  "use strict";

  // ===================================================================
  // Debug logger — structured console output for easier diagnostics.
  // Access window.__streamLog() in the browser console to see all events.
  // ===================================================================
  const _log = [];
  function dbg(category, msg, data) {
    const entry = { t: new Date().toISOString(), cat: category, msg, data };
    _log.push(entry);
    if (_log.length > 400) _log.shift();
    const style = {
      PLAYER: "color:#a78bfa", SYNC: "color:#34d399", SOCKET: "color:#60a5fa",
      CODEC: "color:#fbbf24;font-weight:bold", ERROR: "color:#f87171;font-weight:bold",
      FS: "color:#c084fc", PIP: "color:#f472b6", CHAT: "color:#22d3ee", QUALITY: "color:#fb923c",
    }[category] || "color:#94a3b8";
    console.log(`%c[${category}] ${msg}`, style, data !== undefined ? data : "");
  }
  window.__streamLog = () => {
    console.table(_log.map(e => ({ t: e.t, cat: e.cat, msg: e.msg })));
    return _log;
  };
  console.log("%c[Stream Player] Debug mode active — run __streamLog() to see all events", "color:#a78bfa;font-weight:bold");

  // ===================================================================
  // DOM elements
  // ===================================================================
  const $ = (id) => document.getElementById(id);

  const video = $("video");
  const dubAudio = $("dubAudio");
  const stage = $("stage");
  const stageEmpty = $("stageEmpty");
  const stageEmptyIcon = $("stageEmptyIcon");
  const stageEmptyText = $("stageEmptyText");
  const stageEmptyBtn = $("stageEmptyBtn");
  const subOverlay = $("subOverlay");
  const liveBadge = $("liveBadge");
  const bufferSpinner = $("bufferSpinner");
  const bigPlay = $("bigPlay");

  const seekBar = $("seekBar");
  const curTimeEl = $("curTime");
  const durTimeEl = $("durTime");
  const volBar = $("volBar");

  const btnPlay = $("btnPlay");
  const btnPrev = $("btnPrev");
  const btnNext = $("btnNext");
  const btnBack10 = $("btnBack10");
  const btnFwd10 = $("btnFwd10");
  const btnMute = $("btnMute");
  const btnSpeed = $("btnSpeed");
  const speedMenu = $("speedMenu");
  const btnQuality = $("btnQuality");
  const qualityMenu = $("qualityMenu");
  const btnAudioTrack = $("btnAudioTrack");
  const audioMenu = $("audioMenu");
  const btnSubtitle = $("btnSubtitle");
  const subtitleMenu = $("subtitleMenu");
  const btnPip = $("btnPip");
  const btnFullscreen = $("btnFullscreen");
  const btnShortcuts = $("btnShortcuts");
  const nowPlayingTitle = $("nowPlayingTitle");
  const playerWrap = $("playerWrap");

  const shortcutsModal = $("shortcutsModal");
  const btnCloseShortcuts = $("btnCloseShortcuts");

  const playlistEl = $("playlistEl");
  const playlistEmptyHint = $("playlistEmptyHint");
  const playlistFooter = $("playlistFooter");
  const cleanupStatus = $("cleanupStatus");
  const userList = $("userList");
  const onlineCount = $("onlineCount");
  const onlineCountTop = $("onlineCountTop");
  const notifyFeed = $("notifyFeed");
  const toastContainer = $("toastContainer");

  const nameModal = $("nameModal");
  const nameInput = $("nameInput");
  const btnJoin = $("btnJoin");
  const myNameLabel = $("myNameLabel");
  const btnChangeName = $("btnChangeName");

  const sofaPeople = $("sofaPeople");
  const sofaCouch = $("sofaCouch");

  const myAvatarPreview = $("myAvatarPreview");
  const myAvatarPlaceholder = $("myAvatarPlaceholder");
  const btnChangeAvatar = $("btnChangeAvatar");
  const avatarInput = $("avatarInput");

  const chatFeed = $("chatFeed");
  const chatEmptyHint = $("chatEmptyHint");
  const chatBadge = $("chatBadge");
  const formChat = $("formChat");
  const chatTextInput = $("chatTextInput");
  const btnChatImage = $("btnChatImage");
  const chatImageInput = $("chatImageInput");
  const chatImagePreview = $("chatImagePreview");
  const chatImagePreviewImg = $("chatImagePreviewImg");
  const btnChatImageRemove = $("btnChatImageRemove");
  const btnClearChat = $("btnClearChat");

  // ===================================================================
  // وضعیت کلاینت
  // ===================================================================
  let myName = localStorage.getItem("stream_user_name") || "";
  let myAvatarUrl = localStorage.getItem("stream_user_avatar") || "";
  let socket = null;

  const live = {
    playlist: [],
    current_index: null,
    playing: false,
    position: 0,
    rate: 1,
    online: 0,
    _recvLocal: nowSec(),
  };

  let currentItem = null;     // آیتم پلی‌لیست در حال پخش
  let hls = null;
  let currentSubIndex = -1;   // -1 = خاموش
  let currentAudioTrackId = null;
  let currentAudioTrackUrl = null;
  let isDraggingSeek = false;
  let generatedLive = null;   // {key, push_url, playback_url}
  let pendingChatImageUrl = null;
  let chatTabActive = true;
  let unreadChat = 0;
  let lastFrontierWarnAt = 0;
  let activeLevelLabel = null;   // label (e.g. "720p") of the hls.js level currently playing
  let pendingQualityRequest = null;
  let myRequestedQualityLabels = new Set();
  let qualityMenuNeedsRefresh = false;

  const SPEEDS = [0.5, 0.75, 1, 1.25, 1.5, 1.75, 2];

  function nowSec() { return Date.now() / 1000; }
  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    }[c]));
  }
  function formatTime(sec) {
    sec = Math.max(0, Math.floor(sec || 0));
    const h = Math.floor(sec / 3600);
    const m = Math.floor((sec % 3600) / 60);
    const s = sec % 60;
    const pad = (n) => String(n).padStart(2, "0");
    return h > 0 ? `${h}:${pad(m)}:${pad(s)}` : `${m}:${pad(s)}`;
  }
  function isHlsUrl(url) { return /\.m3u8(\?.*)?$/i.test(url || ""); }
  function hexToRgba(hex, alpha) {
    const m = /^#?([a-f\d]{2})([a-f\d]{2})([a-f\d]{2})$/i.exec(hex || "#000000");
    if (!m) return `rgba(0,0,0,${alpha})`;
    const r = parseInt(m[1], 16), g = parseInt(m[2], 16), b = parseInt(m[3], 16);
    return `rgba(${r},${g},${b},${alpha})`;
  }

  // Deterministic "initials + color" default avatar (no upload required).
  function nameHueColor(name) {
    let h = 0;
    const s = String(name || "?");
    for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) % 360;
    return `hsl(${h}, 62%, 46%)`;
  }
  function nameInitial(name) {
    const s = String(name || "?").trim();
    return s ? s[0].toUpperCase() : "?";
  }
  function avatarNode(name, avatarUrl, sizeClass) {
    const div = document.createElement("div");
    div.className = sizeClass;
    if (avatarUrl) {
      const img = document.createElement("img");
      img.src = avatarUrl;
      img.alt = name || "";
      div.appendChild(img);
    } else {
      div.textContent = nameInitial(name);
      div.style.background = nameHueColor(name);
    }
    return div;
  }

  // ===================================================================
  // ورود به اتاق / نام کاربر
  // ===================================================================
  function showNameModal() { nameModal.hidden = false; nameInput.value = myName || ""; nameInput.focus(); }
  function hideNameModal() { nameModal.hidden = true; }

  function renderMyAvatar() {
    if (myAvatarUrl) {
      myAvatarPreview.src = myAvatarUrl;
      myAvatarPreview.hidden = false;
      myAvatarPlaceholder.hidden = true;
    } else {
      myAvatarPreview.hidden = true;
      myAvatarPlaceholder.hidden = false;
      myAvatarPlaceholder.textContent = nameInitial(myName);
      myAvatarPlaceholder.style.background = nameHueColor(myName);
    }
  }

  function joinRoom(name) {
    myName = name.trim().slice(0, 24) || "ناشناس";
    localStorage.setItem("stream_user_name", myName);
    myNameLabel.textContent = myName;
    renderMyAvatar();
    hideNameModal();
    connectSocket();
  }

  btnJoin.addEventListener("click", () => {
    if (!nameInput.value.trim()) { nameInput.focus(); return; }
    joinRoom(nameInput.value);
  });
  nameInput.addEventListener("keydown", (e) => { if (e.key === "Enter") btnJoin.click(); });
  btnChangeName.addEventListener("click", showNameModal);

  if (myName) {
    myNameLabel.textContent = myName;
    renderMyAvatar();
    connectSocket();
  } else {
    showNameModal();
  }

  // ── Avatar upload ─────────────────────────────────────────────────────
  btnChangeAvatar.addEventListener("click", () => avatarInput.click());
  avatarInput.addEventListener("change", async () => {
    const file = avatarInput.files[0];
    if (!file || !myName) return;
    const fd = new FormData();
    fd.append("file", file);
    fd.append("name", myName);
    try {
      const res = await fetch("/stream/api/avatar", { method: "POST", body: fd });
      const data = await res.json();
      if (res.ok) {
        myAvatarUrl = data.url;
        localStorage.setItem("stream_user_avatar", myAvatarUrl);
        renderMyAvatar();
      } else {
        alert("خطا در آپلود عکس: " + (data.error || ""));
      }
    } catch {
      alert("خطا در ارتباط با سرور");
    }
    avatarInput.value = "";
  });

  // ===================================================================
  // اتصال Socket.IO
  // ===================================================================
  function connectSocket() {
    if (socket) return;
    dbg("SOCKET", "connecting…");
    socket = io({ path: "/stream/socket.io", transports: ["websocket"] });

    socket.on("connect", () => {
      dbg("SOCKET", "connected, sid=" + socket.id);
      socket.emit("join", { name: myName });
    });
    socket.on("connect_error", (err) => dbg("ERROR", "socket connect_error", err.message));
    socket.on("disconnect", (reason) => dbg("SOCKET", "disconnected", reason));

    socket.on("state_sync", (data) => {
      dbg("SYNC", `state_sync: idx=${data.current_index} playing=${data.playing} pos=${data.position?.toFixed(1)}s`);
      live.playlist = data.playlist || [];
      live.current_index = data.current_index;
      live.playing = data.playing;
      live.position = data.position;
      live.rate = data.rate;
      live._recvLocal = nowSec();
      live.online = data.online || 0;

      renderPlaylist();
      renderOnline();
      applyLiveStateToPlayer();
      renderTransportUI();
    });

    socket.on("presence", (data) => {
      live.online = data.online || 0;
      renderOnline(data.users || []);
    });

    socket.on("notify", (n) => {
      dbg("SOCKET", "notify: " + n.type, n.name);
      pushNotify(n);
      pushToast(n);
      // A rendition we (or someone) requested just became available for the
      // item currently loaded in the player — reload hls.js so the new
      // #EXT-X-STREAM-INF entry in master.m3u8 is picked up.
      if ((n.type === "quality_ready" || n.type === "quality_partial_ready") &&
          currentItem && n.id === currentItem.id) {
        if (myRequestedQualityLabels.has(n.label)) {
          reloadHlsForNewQuality(n.label, true);
        } else {
          // Someone else requested it — don't interrupt my playback with a
          // reload right now; just refresh the manifest next time I
          // actually open the quality menu.
          qualityMenuNeedsRefresh = true;
        }
      }
    });

    socket.on("transcode_progress", (data) => {
      updateTranscodeProgress(data.id, data);
    });

    // ── Chat ──
    socket.on("chat_history", (data) => renderChatHistory(data.messages || []));
    socket.on("chat_message", (msg) => appendChatMessage(msg));
    socket.on("chat_cleared", () => { chatFeed.innerHTML = ""; chatEmptyHint.hidden = false; unreadChat = 0; updateChatBadge(); });
    socket.on("chat_purged", () => {
      // A background purge removed some old messages — just re-sync from the server.
      fetch("/stream/api/chat/history").then((r) => r.json()).then((d) => renderChatHistory(d.messages || [])).catch(() => {});
    });
  }

  // ===================================================================
  // ارسال کنترل (optimistic + emit به سرور)
  // ===================================================================
  function expectedPosition() {
    return live.position + (live.playing ? (nowSec() - live._recvLocal) * live.rate : 0);
  }

  // Returns how many seconds of the currently *active rendition* are safely
  // encoded & playable so far, or Infinity once that rendition is fully
  // "complete" (or for item types never progressively encoded, e.g. live/
  // HLS passthrough — those are always fully seekable). Tracked per
  // rendition (not just per item) because different viewers can be on
  // different quality tracks at the same time.
  function encodedFrontier(item) {
    if (!item) return Infinity;
    if (item.type === "live") return Infinity;
    const renditions = item.renditions || [];
    if (!renditions.length) return item.status === "complete" ? Infinity : 0;
    const label = activeLevelLabel || (defaultRenditionOf(item) || {}).label;
    const rend = renditions.find((r) => r.label === label);
    if (!rend) return Infinity;
    if (rend.status === "complete") return Infinity;
    if (rend.status !== "ready") return 0;
    const info = progressFor(item, label);
    if (!info || info.encoded_seconds == null) return Infinity; // unknown -> don't block
    return info.encoded_seconds;
  }

  function clampToFrontier(item, target) {
    const frontier = encodedFrontier(item);
    const SAFETY_MARGIN = 3; // seconds, roughly one HLS segment
    if (target > frontier - SAFETY_MARGIN && frontier !== Infinity) {
      const now = Date.now();
      if (now - lastFrontierWarnAt > 2500) {
        lastFrontierWarnAt = now;
        pushToast({ _text: `⏳ این بخش هنوز آماده نشده — انکد تا دقیقه ${formatTime(frontier)} پیش رفته.`, _warn: true });
      }
      return Math.max(0, frontier - SAFETY_MARGIN);
    }
    return target;
  }

  function requestControl(action, extra) {
    extra = extra || {};
    if (action === "seek" && currentItem) {
      extra.to = clampToFrontier(currentItem, extra.to);
    }
    switch (action) {
      case "play":
        live.playing = true; live.position = extra.at; live._recvLocal = nowSec(); break;
      case "pause":
        live.playing = false; live.position = extra.at; live._recvLocal = nowSec(); break;
      case "seek":
        live.position = extra.to; live._recvLocal = nowSec(); break;
      case "rate":
        live.position = expectedPosition(); live._recvLocal = nowSec(); live.rate = extra.rate; break;
      case "select":
        live.current_index = extra.index; live.position = 0; live.playing = false; live._recvLocal = nowSec(); break;
    }
    applyLiveStateToPlayer();
    renderTransportUI();
    renderPlaylist();
    if (socket) socket.emit("control", Object.assign({ action, name: myName }, extra));
  }

  // ===================================================================
  // پلیر — بارگذاری آیتم
  // ===================================================================
  function destroyHls() { if (hls) { hls.destroy(); hls = null; } }

  function levelLabelFromUrl(url) {
    const m = /\/([^/]+)\/index\.m3u8/.exec(url || "");
    return m ? m[1] : null;
  }
  function defaultRenditionOf(item) {
    const rs = (item && item.renditions) || [];
    return rs.find((r) => r.is_default) || rs[0] || null;
  }
  function progressFor(item, label) {
    return item && label ? _transcodeProgress[item.id + ":" + label] : undefined;
  }

  function setupSource(src) {
    destroyHls();
    activeLevelLabel = null;
    myRequestedQualityLabels = new Set();
    qualityMenuNeedsRefresh = false;
    qualityMenu.innerHTML = "";
    btnQuality.disabled = true;
    if (isHlsUrl(src) && window.Hls && window.Hls.isSupported()) {
      dbg("PLAYER", "HLS source detected, using hls.js", src);
      hls = new Hls({ liveSyncDurationCount: 6 });
      hls.on(Hls.Events.ERROR, (ev, data) => {
        dbg("ERROR", "hls.js error", { type: data.type, details: data.details, fatal: data.fatal });
      });
      hls.on(Hls.Events.MANIFEST_PARSED, () => buildQualityMenu());
      hls.on(Hls.Events.LEVEL_SWITCHED, (ev, data) => {
        const lvl = hls.levels[data.level];
        activeLevelLabel = lvl ? levelLabelFromUrl(lvl.url) : null;
        dbg("QUALITY", "level switched", { level: data.level, label: activeLevelLabel });
        buildQualityMenu();
      });
      hls.loadSource(src);
      hls.attachMedia(video);
    } else if (isHlsUrl(src) && video.canPlayType("application/vnd.apple.mpegurl")) {
      // Safari's native HLS — it handles ABR internally with no manual
      // level-selection API, so we just leave the quality button disabled.
      dbg("PLAYER", "Native HLS playback (Safari)", src);
      video.src = src;
    } else {
      dbg("PLAYER", "Direct src assigned", src);
      video.src = src;
    }
  }

  // Called when the server tells us (via socket "notify") that a rendition
  // we're watching (or might want) just became ready/partially-ready. We
  // have to reload the whole hls.js source because master.m3u8 gained a
  // new #EXT-X-STREAM-INF entry that our already-loaded instance doesn't
  // know about — hls.js has no API to hot-add a level to a live instance.
  function reloadHlsForNewQuality(label, autoSwitch) {
    if (!hls || !currentItem || !currentItem.src) return;
    const resumeAt = video.currentTime;
    const wasPlaying = !video.paused;
    const onParsed = () => {
      hls.off(Hls.Events.MANIFEST_PARSED, onParsed);
      if (autoSwitch) {
        const idx = hls.levels.findIndex((lvl) => levelLabelFromUrl(lvl.url) === label);
        if (idx !== -1) hls.currentLevel = idx;
      }
      try { video.currentTime = resumeAt; } catch (e) {}
      if (wasPlaying) video.play().catch(() => {});
      buildQualityMenu();
    };
    hls.on(Hls.Events.MANIFEST_PARSED, onParsed);
    hls.loadSource(currentItem.src);
  }

  function buildQualityMenu() {
    qualityMenu.innerHTML = "";
    const renditions = (currentItem && currentItem.renditions) || [];

    if (!renditions.length) {
      // No ladder info (legacy item, or a plain HLS passthrough source) —
      // fall back to whatever hls.js itself discovered in the manifest.
      if (!hls || !hls.levels || !hls.levels.length) { btnQuality.disabled = true; return; }
      btnQuality.disabled = false;
      const cur = hls.currentLevel;
      addMenuItem(qualityMenu, "🔀 خودکار", cur === -1, () => { hls.currentLevel = -1; buildQualityMenu(); });
      qualityMenu.appendChild(divider());
      const order = hls.levels.map((_, i) => i).sort((a, b) => (hls.levels[b].height || 0) - (hls.levels[a].height || 0));
      order.forEach((i) => {
        const lvl = hls.levels[i];
        addMenuItem(qualityMenu, lvl.height ? `${lvl.height}p` : `سطح ${i + 1}`, cur === i, () => {
          hls.currentLevel = i; buildQualityMenu();
        });
      });
      return;
    }

    btnQuality.disabled = false;
    const cur = hls ? hls.currentLevel : -1;
    addMenuItem(qualityMenu, "🔀 خودکار", !!hls && cur === -1, () => { if (hls) hls.currentLevel = -1; buildQualityMenu(); });
    qualityMenu.appendChild(divider());

    [...renditions].sort((a, b) => b.height - a.height).forEach((r) => {
      if (r.status === "ready" || r.status === "complete") {
        const idx = hls ? hls.levels.findIndex((lvl) => levelLabelFromUrl(lvl.url) === r.label) : -1;
        const active = !!hls && idx !== -1 && cur === idx;
        const div = document.createElement("div");
        div.className = "menu-item" + (active ? " active" : "");
        div.textContent = r.label + (active ? " ✓" : "");
        if (idx !== -1) {
          div.addEventListener("click", () => { hls.currentLevel = idx; closeAllMenus(); buildQualityMenu(); });
        } else {
          div.style.opacity = "0.5";
        }
        qualityMenu.appendChild(div);
      } else if (r.status === "pending" || r.status === "error") {
        const div = document.createElement("div");
        div.className = "menu-item";
        div.innerHTML = `${esc(r.label)} <span class="menu-item-badge">${r.status === "error" ? "خطا — دوباره امتحان کن" : "برای آماده‌سازی کلیک کن"}</span>`;
        div.addEventListener("click", () => { requestQuality(currentItem.id, r.label); closeAllMenus(); });
        qualityMenu.appendChild(div);
      } else {
        // "queued" or "encoding" — already in progress, not clickable.
        const div = document.createElement("div");
        div.className = "menu-item";
        div.style.opacity = "0.6"; div.style.cursor = "default";
        div.innerHTML = `${esc(r.label)} <span class="menu-item-badge">⏳ در حال آماده‌سازی…</span>`;
        qualityMenu.appendChild(div);
      }
    });
  }

  async function requestQuality(itemId, label) {
    if (pendingQualityRequest === itemId + ":" + label) return;
    pendingQualityRequest = itemId + ":" + label;
    try {
      const res = await fetch("/stream/api/request-quality", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ item_id: itemId, label, name: myName }),
      });
      if (res.ok) {
        myRequestedQualityLabels.add(label);
        pushToast({ _text: `⏳ آماده‌سازی کیفیت ${label} شروع شد…` });
      } else {
        const data = await res.json().catch(() => ({}));
        pushToast({ _text: `❌ ${data.error || "شروع آماده‌سازی این کیفیت ناموفق بود"}`, _warn: true });
      }
    } catch {
      pushToast({ _text: "❌ خطا در ارتباط با سرور", _warn: true });
    }
    pendingQualityRequest = null;
  }

  function clearTextTracks() {
    [...video.querySelectorAll("track")].forEach((t) => t.remove());
  }

  function loadItem(item) {
    currentItem = item;
    currentSubIndex = -1;
    stopDubAudio();
    currentAudioTrackId = null;
    clearTextTracks();
    subOverlay.innerHTML = "";

    dbg("PLAYER", `loadItem: ${item.title}`, { type: item.type, src: item.src, status: item.status });
    setupSource(item.src);

    (item.subtitles || []).forEach((s) => {
      const track = document.createElement("track");
      track.kind = "subtitles";
      track.label = s.label;
      track.srclang = s.lang || "fa";
      track.src = s.url;
      track.crossOrigin = "anonymous";
      video.appendChild(track);
      dbg("PLAYER", `subtitle track added: ${s.label}`, s.url);
    });
    setTimeout(() => {
      for (const t of video.textTracks) t.mode = "showing";
    }, 100);

    liveBadge.hidden = item.type !== "live";
    stageEmpty.hidden = true;
    nowPlayingTitle.textContent = item.title || "";
    btnSpeed.disabled = item.type === "live";
    seekBar.disabled = item.type === "live";

    buildSubtitleMenu(item);
    buildAudioMenu(item);
  }

  function unloadPlayer() {
    currentItem = null;
    destroyHls();
    clearTextTracks();
    video.removeAttribute("src");
    video.load();
    stopDubAudio();
    stageEmpty.hidden = false;
    bufferSpinner.hidden = true;
    stageEmptyIcon.textContent = "🎬";
    stageEmptyText.textContent = "هنوز ویدیویی برای پخش انتخاب نشده";
    stageEmptyBtn.hidden = false;
    liveBadge.hidden = true;
    nowPlayingTitle.textContent = "چیزی پخش نمی‌شود";
    subOverlay.innerHTML = "";
  }

  // Map of "itemId:label" -> latest transcode_progress payload (kept for
  // re-renders and for the per-rendition seek-frontier guard).
  const _transcodeProgress = {};

  function updateTranscodeProgress(itemId, data) {
    const label = data.label;
    _transcodeProgress[itemId + ":" + label] = data;
    const pct = data.pct ?? 0;

    // Playlist row + stage overlay only ever show the DEFAULT rendition's
    // progress, since that's the one that determines when the item as a
    // whole becomes watchable. Progress on an on-demand rendition someone
    // requested later only needs to update the quality menu (handled via
    // buildQualityMenu()/the seek guard reading _transcodeProgress directly).
    const item = live.playlist.find((it) => it.id === itemId);
    const def = item ? defaultRenditionOf(item) : null;
    if (!def || def.label !== label) return;

    const bar = document.querySelector(`.pl-progress-fill[data-id="${itemId}"]`);
    if (bar) bar.style.width = pct + "%";
    const pctEl = document.querySelector(`.pl-progress-pct[data-id="${itemId}"]`);
    if (pctEl) pctEl.textContent = pct + "%";
    if (live.playlist[live.current_index] && live.playlist[live.current_index].id === itemId &&
        (item.status === "queued" || item.status === "encoding")) {
      _updateStageProgressText(pct, item.title || "");
    }
  }

  function _updateStageProgressText(pct, title) {
    stageEmptyIcon.textContent = "⏳";
    stageEmptyText.textContent = `در حال آماده‌سازی «${title}» — پخش معمولاً ظرف حدود ۲ دقیقه شروع می‌شه`;
    const bar = document.getElementById("stageProgressBar");
    if (bar) bar.style.width = pct + "%";
    const pctEl = document.getElementById("stageProgressPct");
    if (pctEl) pctEl.textContent = pct + "%";
  }

  function showProcessingOverlay(item) {
    destroyHls();
    clearTextTracks();
    video.removeAttribute("src");
    video.load();
    stopDubAudio();
    stageEmpty.hidden = false;
    stageEmptyBtn.hidden = true;
    liveBadge.hidden = true;
    bufferSpinner.hidden = true;
    if (item.status === "error") {
      stageEmptyIcon.textContent = "⚠️";
      stageEmptyText.textContent = "تبدیل این ویدیو ناموفق بود: " + (item.error || "خطای نامشخص");
      document.getElementById("stageProgressWrap").hidden = true;
    } else {
      const def = defaultRenditionOf(item);
      const pct = progressFor(item, def && def.label)?.pct ?? 0;
      document.getElementById("stageProgressWrap").hidden = false;
      _updateStageProgressText(pct, item.title || "");
    }
    nowPlayingTitle.textContent = item.title || "";
  }

  function applyLiveStateToPlayer() {
    if (live.current_index === null || live.current_index === undefined || !live.playlist[live.current_index]) {
      if (currentItem) unloadPlayer();
      return;
    }
    const item = live.playlist[live.current_index];

    // "queued"/"encoding" -> not playable yet at all.
    if (item.status === "queued" || item.status === "encoding" || item.status === "error") {
      currentItem = item;
      showProcessingOverlay(item);
      return;
    }

    // "ready" (still encoding in the background) or "complete" -> playable.
    const needsReload = !currentItem || currentItem.id !== item.id ||
      (currentItem.status !== "ready" && currentItem.status !== "complete");
    if (needsReload) {
      loadItem(item);
    } else {
      currentItem = item; // refresh reference (renditions/status may have changed) without reloading src
      buildQualityMenu();
    }
    video.playbackRate = live.rate;
    if (currentAudioTrackId) dubAudio.playbackRate = live.rate;

    const expected = expectedPosition();
    if (item.type !== "live" && isFinite(expected)) {
      const target = clampToFrontier(item, expected);
      const diff = Math.abs((video.currentTime || 0) - target);
      if (diff > 1.2) {
        try { video.currentTime = target; } catch (e) { /* metadata may not be ready yet */ }
      }
    }

    if (live.playing && video.paused) {
      const p = video.play();
      if (p && p.catch) p.catch(() => { bigPlay.hidden = false; });
    } else if (!live.playing && !video.paused) {
      video.pause();
    }
  }

  // درایفت پریودیک برای جلوگیری از خارج‌شدن از سینک
  setInterval(() => { if (currentItem) applyLiveStateToPlayer(); }, 6000);

  video.addEventListener("loadedmetadata", () => {
    dbg("CODEC", "loadedmetadata", { duration: video.duration, videoWidth: video.videoWidth, videoHeight: video.videoHeight });
    durTimeEl.textContent = formatTime(video.duration);
    seekBar.max = isFinite(video.duration) ? video.duration : 0;
    applyLiveStateToPlayer();
  });

  video.addEventListener("waiting", () => { dbg("PLAYER", "buffering..."); bufferSpinner.hidden = false; });
  video.addEventListener("playing", () => {
    dbg("PLAYER", "playing", { currentTime: video.currentTime, playbackRate: video.playbackRate });
    bufferSpinner.hidden = true; bigPlay.hidden = true;
  });
  video.addEventListener("canplay", () => { dbg("PLAYER", "canplay"); bufferSpinner.hidden = true; });
  video.addEventListener("stalled", () => dbg("PLAYER", "stalled (network stall)"));
  video.addEventListener("suspend", () => dbg("PLAYER", "suspend (browser paused loading)"));
  video.addEventListener("emptied", () => dbg("PLAYER", "emptied (src cleared)"));

  video.addEventListener("error", () => {
    const err = video.error;
    if (!err) return;
    const CODES = {
      1: "MEDIA_ERR_ABORTED — playback aborted by user",
      2: "MEDIA_ERR_NETWORK — network error",
      3: "MEDIA_ERR_DECODE — decoding failed (likely unsupported codec or corrupt file)",
      4: "MEDIA_ERR_SRC_NOT_SUPPORTED — format/codec not supported by this browser",
    };
    dbg("ERROR", `video error code ${err.code}: ${CODES[err.code] || "unknown"}`, { message: err.message, src: video.currentSrc });
    if (err.code === 3 || err.code === 4) {
      pushToast({ type: "codec_error", name: "پلیر", _text: `❌ مرورگر این فایل رو پخش نمی‌کنه (code ${err.code}). جزئیات در Console مرورگر (F12).` });
    }
  });

  video.addEventListener("timeupdate", () => {
    if (!isDraggingSeek) {
      seekBar.value = video.currentTime || 0;
      const pct = video.duration ? (video.currentTime / video.duration) * 100 : 0;
      seekBar.style.backgroundSize = pct + "% 100%";
    }
    curTimeEl.textContent = formatTime(video.currentTime);
    renderActiveSubtitle();
  });

  bigPlay.addEventListener("click", () => { bigPlay.hidden = true; togglePlay(); });

  // ===================================================================
  // زیرنویس
  // ===================================================================
  function stripVttTags(text) { return String(text || "").replace(/<[^>]+>/g, ""); }

  function renderActiveSubtitle() {
    subOverlay.innerHTML = "";
    if (currentSubIndex < 0) return;
    const track = video.textTracks[currentSubIndex];
    if (!track || !track.activeCues) return;
    for (const c of track.activeCues) {
      const div = document.createElement("div");
      div.className = "sub-line";
      div.textContent = stripVttTags(c.text);
      subOverlay.appendChild(div);
    }
  }

  function selectSubtitle(index) {
    currentSubIndex = index;
    buildSubtitleMenu(currentItem);
  }

  function buildSubtitleMenu(item) {
    subtitleMenu.innerHTML = "";
    addMenuItem(subtitleMenu, "🚫 خاموش", currentSubIndex === -1, () => selectSubtitle(-1));
    const subs = (item && item.subtitles) || [];
    if (subs.length) subtitleMenu.appendChild(divider());
    subs.forEach((s, i) => addMenuItem(subtitleMenu, s.label, currentSubIndex === i, () => selectSubtitle(i)));
  }

  // ---- استایل زیرنویس (تنظیمات شخصی، فقط لوکال) ----
  const DEFAULT_SUB_STYLE = {
    font: "Vazirmatn", size: 28, color: "#ffffff", bg: "#000000",
    bgOpacity: 60, outline: true, bold: false, offset: 40,
  };
  let subStyle = loadSubStyle();

  function loadSubStyle() {
    try {
      const raw = localStorage.getItem("stream_sub_style");
      return raw ? Object.assign({}, DEFAULT_SUB_STYLE, JSON.parse(raw)) : Object.assign({}, DEFAULT_SUB_STYLE);
    } catch (e) { return Object.assign({}, DEFAULT_SUB_STYLE); }
  }
  function saveSubStyle() { localStorage.setItem("stream_sub_style", JSON.stringify(subStyle)); }

  function applySubStyleToEl(el) {
    const fontMap = { Vazirmatn: "'Vazirmatn'", JetBrainsMono: "'JetBrainsMono'", Tahoma: "Tahoma", "system-ui": "system-ui" };
    el.style.setProperty("--sub-font", fontMap[subStyle.font] || "'Vazirmatn'");
    el.style.setProperty("--sub-size", subStyle.size + "px");
    el.style.setProperty("--sub-color", subStyle.color);
    el.style.setProperty("--sub-bg", hexToRgba(subStyle.bg, subStyle.bgOpacity / 100));
    el.style.setProperty("--sub-weight", subStyle.bold ? "700" : "400");
    el.style.setProperty("--sub-shadow", subStyle.outline
      ? "0 0 3px rgba(0,0,0,.95), 0 0 7px rgba(0,0,0,.8), 0 1px 2px rgba(0,0,0,.9)"
      : "none");
  }
  function applySubStyleEverywhere() {
    applySubStyleToEl(subOverlay);
    applySubStyleToEl($("subPreview"));
    subOverlay.style.bottom = subStyle.offset + "px";
  }

  function initSubStylePanel() {
    $("subFont").value = subStyle.font;
    $("subSize").value = subStyle.size; $("subSizeVal").textContent = subStyle.size;
    $("subColor").value = subStyle.color;
    $("subBg").value = subStyle.bg;
    $("subBgOpacity").value = subStyle.bgOpacity; $("subBgOpacityVal").textContent = subStyle.bgOpacity;
    $("subOutline").checked = subStyle.outline;
    $("subBold").checked = subStyle.bold;
    $("subOffset").value = subStyle.offset; $("subOffsetVal").textContent = subStyle.offset;
    applySubStyleEverywhere();

    $("subFont").addEventListener("change", (e) => { subStyle.font = e.target.value; saveSubStyle(); applySubStyleEverywhere(); });
    $("subSize").addEventListener("input", (e) => { subStyle.size = +e.target.value; $("subSizeVal").textContent = subStyle.size; saveSubStyle(); applySubStyleEverywhere(); });
    $("subColor").addEventListener("input", (e) => { subStyle.color = e.target.value; saveSubStyle(); applySubStyleEverywhere(); });
    $("subBg").addEventListener("input", (e) => { subStyle.bg = e.target.value; saveSubStyle(); applySubStyleEverywhere(); });
    $("subBgOpacity").addEventListener("input", (e) => { subStyle.bgOpacity = +e.target.value; $("subBgOpacityVal").textContent = subStyle.bgOpacity; saveSubStyle(); applySubStyleEverywhere(); });
    $("subOutline").addEventListener("change", (e) => { subStyle.outline = e.target.checked; saveSubStyle(); applySubStyleEverywhere(); });
    $("subBold").addEventListener("change", (e) => { subStyle.bold = e.target.checked; saveSubStyle(); applySubStyleEverywhere(); });
    $("subOffset").addEventListener("input", (e) => { subStyle.offset = +e.target.value; $("subOffsetVal").textContent = subStyle.offset; saveSubStyle(); applySubStyleEverywhere(); });
    $("btnSubReset").addEventListener("click", () => {
      subStyle = Object.assign({}, DEFAULT_SUB_STYLE); saveSubStyle(); initSubStylePanel();
    });
  }

  // ===================================================================
  // کانال‌های صدا (دوبله)
  // ===================================================================
  function stopDubAudio() {
    dubAudio.pause();
    dubAudio.removeAttribute("src");
    dubAudio.load();
  }

  function selectAudioTrack(id, url) {
    currentAudioTrackId = id;
    currentAudioTrackUrl = url || null;
    if (!id) {
      video.muted = false;
      stopDubAudio();
    } else {
      video.muted = true;
      dubAudio.src = url;
      dubAudio.currentTime = video.currentTime || 0;
      dubAudio.playbackRate = video.playbackRate;
      dubAudio.volume = video.volume;
      if (!video.paused) dubAudio.play().catch(() => {});
    }
    buildAudioMenu(currentItem);
  }

  function buildAudioMenu(item) {
    audioMenu.innerHTML = "";
    addMenuItem(audioMenu, "🎙 صدای اصلی", !currentAudioTrackId, () => selectAudioTrack(null));
    const tracks = (item && item.audio_tracks) || [];
    if (tracks.length) audioMenu.appendChild(divider());
    tracks.forEach((t) => addMenuItem(audioMenu, t.label, currentAudioTrackId === t.id, () => selectAudioTrack(t.id, t.url)));
  }

  video.addEventListener("play", () => { if (currentAudioTrackId && dubAudio.paused) dubAudio.play().catch(() => {}); });
  video.addEventListener("pause", () => { if (currentAudioTrackId) dubAudio.pause(); });
  video.addEventListener("seeked", () => { if (currentAudioTrackId) dubAudio.currentTime = video.currentTime; });
  video.addEventListener("ratechange", () => { if (currentAudioTrackId) dubAudio.playbackRate = video.playbackRate; });
  setInterval(() => {
    if (currentAudioTrackId && !dubAudio.paused && !video.paused) {
      if (Math.abs(dubAudio.currentTime - video.currentTime) > 0.3) dubAudio.currentTime = video.currentTime;
    }
  }, 2000);

  // ===================================================================
  // منوهای کشویی عمومی
  // ===================================================================
  function divider() { const d = document.createElement("div"); d.className = "menu-divider"; return d; }
  function addMenuItem(menuEl, label, active, onClick) {
    const div = document.createElement("div");
    div.className = "menu-item" + (active ? " active" : "");
    div.textContent = label + (active ? " ✓" : "");
    div.addEventListener("click", () => { onClick(); closeAllMenus(); });
    menuEl.appendChild(div);
  }
  function closeAllMenus() { document.querySelectorAll(".menu.open").forEach((m) => m.classList.remove("open")); }
  function toggleMenu(menuEl) {
    const wasOpen = menuEl.classList.contains("open");
    closeAllMenus();
    if (!wasOpen) menuEl.classList.add("open");
  }
  btnSpeed.addEventListener("click", () => toggleMenu(speedMenu));
  btnQuality.addEventListener("click", () => {
    if (btnQuality.disabled) return;
    if (qualityMenuNeedsRefresh && hls && currentItem) {
      qualityMenuNeedsRefresh = false;
      reloadHlsForNewQuality(null, false);
    }
    toggleMenu(qualityMenu);
  });
  btnAudioTrack.addEventListener("click", () => toggleMenu(audioMenu));
  btnSubtitle.addEventListener("click", () => toggleMenu(subtitleMenu));
  document.addEventListener("click", (e) => {
    if (!e.target.closest(".menu-wrap")) closeAllMenus();
  });

  function buildSpeedMenu() {
    speedMenu.innerHTML = "";
    SPEEDS.forEach((s) => addMenuItem(speedMenu, s + "x", live.rate === s, () => requestControl("rate", { rate: s })));
  }

  // ===================================================================
  // کنترل‌های پخش
  // ===================================================================
  function togglePlay() {
    if (!currentItem) return;
    if (video.paused) requestControl("play", { at: video.currentTime || 0 });
    else requestControl("pause", { at: video.currentTime || 0 });
  }
  btnPlay.addEventListener("click", togglePlay);
  video.addEventListener("click", togglePlay);

  btnPrev.addEventListener("click", () => {
    if (live.current_index === null || live.current_index <= 0) return;
    requestControl("select", { index: live.current_index - 1 });
  });
  btnNext.addEventListener("click", () => {
    if (live.current_index === null || live.current_index >= live.playlist.length - 1) return;
    requestControl("select", { index: live.current_index + 1 });
  });
  btnBack10.addEventListener("click", () => {
    if (!currentItem) return;
    requestControl("seek", { to: Math.max(0, (video.currentTime || 0) - 10) });
  });
  btnFwd10.addEventListener("click", () => {
    if (!currentItem) return;
    requestControl("seek", { to: Math.min(video.duration || 1e9, (video.currentTime || 0) + 10) });
  });

  seekBar.addEventListener("mousedown", () => { isDraggingSeek = true; });
  seekBar.addEventListener("touchstart", () => { isDraggingSeek = true; });
  seekBar.addEventListener("input", () => {
    const v = parseFloat(seekBar.value);
    curTimeEl.textContent = formatTime(v);
    try { video.currentTime = v; } catch (e) {}
  });
  function commitSeek() {
    isDraggingSeek = false;
    if (!currentItem || currentItem.type === "live") return;
    requestControl("seek", { to: parseFloat(seekBar.value) });
  }
  seekBar.addEventListener("change", commitSeek);
  seekBar.addEventListener("mouseup", commitSeek);
  seekBar.addEventListener("touchend", commitSeek);

  function updateMuteIcon(vol) { btnMute.textContent = vol > 0 ? "🔊" : "🔇"; }
  volBar.addEventListener("input", () => {
    const v = parseFloat(volBar.value);
    if (currentAudioTrackId) { dubAudio.volume = v; dubAudio.muted = false; }
    else { video.volume = v; video.muted = false; }
    updateMuteIcon(v);
  });
  btnMute.addEventListener("click", () => {
    if (currentAudioTrackId) { dubAudio.muted = !dubAudio.muted; updateMuteIcon(dubAudio.muted ? 0 : dubAudio.volume); }
    else { video.muted = !video.muted; updateMuteIcon(video.muted ? 0 : video.volume); }
  });

  // ── Fullscreen ─────────────────────────────────────────────────────────
  function _isFullscreen() {
    return !!(document.fullscreenElement || document.webkitFullscreenElement || document.mozFullScreenElement);
  }
  function _enterFullscreen() {
    const el = playerWrap;
    const req = el.requestFullscreen || el.webkitRequestFullscreen || el.mozRequestFullScreen || el.msRequestFullscreen;
    if (req) req.call(el).catch(() => {});
  }
  function _exitFullscreen() {
    const ex = document.exitFullscreen || document.webkitExitFullscreen || document.mozCancelFullScreen || document.msExitFullscreen;
    if (ex) ex.call(document).catch(() => {});
  }
  function _onFsChange() {
    const isFs = _isFullscreen();
    playerWrap.classList.toggle("is-fullscreen", isFs);
    btnFullscreen.title = isFs ? "خروج از تمام‌صفحه (F)" : "تمام‌صفحه (F)";
    if (!isFs) {
      playerWrap.classList.remove("cursor-hidden", "ctrl-visible");
      clearTimeout(_cursorTimer);
    } else {
      _resetCursorTimer();
    }
  }
  ["fullscreenchange", "webkitfullscreenchange", "mozfullscreenchange"].forEach(
    (ev) => document.addEventListener(ev, _onFsChange)
  );
  btnFullscreen.addEventListener("click", () => { if (_isFullscreen()) _exitFullscreen(); else _enterFullscreen(); });

  let _cursorTimer;
  function _resetCursorTimer() {
    playerWrap.classList.remove("cursor-hidden");
    playerWrap.classList.add("ctrl-visible");
    clearTimeout(_cursorTimer);
    if (_isFullscreen()) {
      _cursorTimer = setTimeout(() => {
        playerWrap.classList.add("cursor-hidden");
        playerWrap.classList.remove("ctrl-visible");
      }, 3000);
    }
  }
  playerWrap.addEventListener("mousemove", _resetCursorTimer);
  playerWrap.addEventListener("mousedown", _resetCursorTimer);
  playerWrap.addEventListener("touchstart", () => { _resetCursorTimer(); }, { passive: true });

  btnPip.addEventListener("click", async () => {
    if (!currentItem || !video.src) return;
    try {
      if (document.pictureInPictureElement) await document.exitPictureInPicture();
      else if (video.requestPictureInPicture) await video.requestPictureInPicture();
    } catch (e) {
      console.warn("[PiP]", e);
    }
  });

  function toggleShortcuts(force) {
    shortcutsModal.hidden = (force !== undefined) ? !force : !shortcutsModal.hidden;
  }
  btnShortcuts.addEventListener("click", () => toggleShortcuts());
  btnCloseShortcuts.addEventListener("click", () => toggleShortcuts(false));
  shortcutsModal.addEventListener("click", (e) => { if (e.target === shortcutsModal) toggleShortcuts(false); });

  document.addEventListener("keydown", (e) => {
    const tag = (e.target.tagName || "").toLowerCase();
    if (["input", "textarea", "select"].includes(tag)) return;
    if (e.ctrlKey || e.metaKey || e.altKey) return;

    switch (e.key) {
      case " ":
      case "k":
        e.preventDefault();
        if (currentItem) togglePlay();
        break;
      case "ArrowLeft":
      case "j":
        e.preventDefault();
        if (currentItem && currentItem.type !== "live")
          requestControl("seek", { to: Math.max(0, (video.currentTime || 0) - 10) });
        break;
      case "ArrowRight":
      case "l":
        e.preventDefault();
        if (currentItem && currentItem.type !== "live")
          requestControl("seek", { to: Math.min(video.duration || 1e9, (video.currentTime || 0) + 10) });
        break;
      case "ArrowUp":
        e.preventDefault();
        volBar.value = Math.min(1, parseFloat(volBar.value) + 0.05);
        volBar.dispatchEvent(new Event("input"));
        break;
      case "ArrowDown":
        e.preventDefault();
        volBar.value = Math.max(0, parseFloat(volBar.value) - 0.05);
        volBar.dispatchEvent(new Event("input"));
        break;
      case "m": case "M": btnMute.click(); break;
      case "f": case "F": btnFullscreen.click(); break;
      case "p": case "P": btnPrev.click(); break;
      case "n": case "N": btnNext.click(); break;
      case "?": toggleShortcuts(); break;
      case "Escape": toggleShortcuts(false); break;
      case "0": case "1": case "2": case "3": case "4":
      case "5": case "6": case "7": case "8": case "9": {
        if (currentItem && currentItem.type !== "live" && isFinite(video.duration)) {
          e.preventDefault();
          const pct = parseInt(e.key, 10) / 10;
          requestControl("seek", { to: video.duration * pct });
        }
        break;
      }
    }
    if (document.fullscreenElement) _resetCursorTimer();
  });

  // ===================================================================
  // رندر UI
  // ===================================================================
  function renderTransportUI() {
    btnPlay.textContent = live.playing ? "⏸" : "▶";
    btnSpeed.textContent = live.rate + "x";
    buildSpeedMenu();
  }

  const STATUS_LABEL = {
    queued: "در صف انکد", encoding: "در حال آماده‌سازی",
    ready: "پخش‌پذیر", error: "خطا در تبدیل",
  };

  function renderPlaylist() {
    playlistEl.innerHTML = "";
    const hasItems = live.playlist.length > 0;
    playlistEmptyHint.hidden = hasItems;
    playlistFooter.hidden = !hasItems;
    live.playlist.forEach((item, idx) => {
      const li = document.createElement("li");
      li.className = "playlist-item" + (idx === live.current_index ? " active" : "");
      let icon = item.type === "live" ? "🔴"
               : item.type === "youtube" ? "▶️"
               : item.type === "url" ? "🔗"
               : "🎬";
      let statusBadge = "";
      let progressBar = "";
      if (item.status === "queued" || item.status === "encoding") {
        icon = "⏳";
        const pct = progressFor(item, (defaultRenditionOf(item) || {}).label)?.pct ?? 0;
        statusBadge = `<span class="pl-badge pl-badge-processing"><span class="pl-progress-pct" data-id="${esc(item.id)}">${pct}%</span> ${esc(STATUS_LABEL[item.status] || "")}</span>`;
        progressBar = `
          <div class="pl-progress-track">
            <div class="pl-progress-fill" data-id="${esc(item.id)}" style="width:${pct}%"></div>
          </div>`;
      } else if (item.status === "ready") {
        icon = "▶️";
        statusBadge = `<span class="pl-badge pl-badge-ready">پخش‌پذیر</span>`;
      } else if (item.status === "error") {
        icon = "⚠️";
        statusBadge = `<span class="pl-badge pl-badge-error" title="${esc(item.error || "")}">خطا در تبدیل</span>`;
      }
      li.innerHTML = `
        <div class="pl-icon">${icon}</div>
        <div class="pl-info">
          <div class="pl-title">${esc(item.title)}</div>
          <div class="pl-meta">افزوده‌شده توسط ${esc(item.added_by || "ناشناس")} ${statusBadge}</div>
          ${progressBar}
        </div>
        <button class="pl-remove" title="حذف">✕</button>
      `;
      li.querySelector(".pl-info").addEventListener("click", () => requestControl("select", { index: idx }));
      li.querySelector(".pl-icon").addEventListener("click", () => requestControl("select", { index: idx }));
      li.querySelector(".pl-remove").addEventListener("click", (e) => {
        e.stopPropagation();
        if (!confirm("این آیتم از پلی‌لیست حذف شود؟")) return;
        fetch(`/stream/api/playlist/${item.id}?name=${encodeURIComponent(myName)}`, { method: "DELETE" });
      });
      playlistEl.appendChild(li);
    });
    populateSubTargetSelect();
  }

  function renderOnline(users) {
    onlineCount.textContent = live.online;
    onlineCountTop.textContent = live.online;
    if (users) {
      userList.innerHTML = "";
      users.forEach((u) => {
        const li = document.createElement("li");
        li.className = "user-chip";
        const av = document.createElement("span");
        av.className = "chip-avatar";
        if (u.avatar_url) {
          const img = document.createElement("img");
          img.src = u.avatar_url; img.alt = "";
          av.appendChild(img);
        } else {
          av.textContent = nameInitial(u.name);
          av.style.background = nameHueColor(u.name);
        }
        li.appendChild(av);
        const dot = document.createElement("span");
        dot.className = "dot";
        li.appendChild(dot);
        li.appendChild(document.createTextNode(u.name));
        userList.appendChild(li);
      });
      renderSofa(users);
    }
  }

  // ── مبل کاربران آنلاین ──────────────────────────────────────────────
  function renderSofa(users) {
    sofaPeople.innerHTML = "";
    if (!users.length) {
      const p = document.createElement("p");
      p.className = "sofa-empty-hint";
      p.textContent = "فعلاً کسی روی مبل نیست…";
      sofaPeople.appendChild(p);
      sofaCouch.style.width = "160px";
      return;
    }
    users.forEach((u) => {
      const wrap = document.createElement("div");
      wrap.className = "sofa-person";
      const cushion = document.createElement("div");
      cushion.className = "sofa-cushion";
      const av = avatarNode(u.name, u.avatar_url, "sofa-avatar");
      const nameEl = document.createElement("div");
      nameEl.className = "sofa-name";
      nameEl.textContent = u.name;
      wrap.appendChild(cushion);
      wrap.appendChild(av);
      wrap.appendChild(nameEl);
      sofaPeople.appendChild(wrap);
    });
    // Couch width grows/shrinks with the number of people sitting on it.
    const width = Math.min(1000, Math.max(160, users.length * 62 + 40));
    sofaCouch.style.width = width + "px";
  }

  function notifyText(n) {
    if (n._text) return n._text;
    const name = esc(n.name || "کسی");
    const extra = n.extra || {};
    switch (n.type) {
      case "join": return `👋 <b>${name}</b> به اتاق پیوست`;
      case "leave": return `🚪 <b>${name}</b> از اتاق خارج شد`;
      case "playlist_add": return `➕ <b>${name}</b> «${esc(n.title || "")}» را اضافه کرد`;
      case "playlist_add_processing": return `⏳ <b>${name}</b> «${esc(n.title || "")}» را اضافه کرد (در حال آماده‌سازی روی سرور…)`;
      case "media_partial_ready": return `▶️ «${esc(n.title || "")}» قابل پخش شد (کیفیت پیش‌فرض — بقیه‌ی کیفیت‌ها on-demand هستن)`;
      case "media_ready": return `✅ ویدیوی «${esc(n.title || "")}» کامل آماده‌ی پخش شد`;
      case "media_error": return `⚠️ تبدیل «${esc(n.title || "")}» با خطا مواجه شد`;
      case "quality_partial_ready": return `🖼 کیفیت ${esc(n.label || "")} برای «${esc(n.title || "")}» قابل پخش شد`;
      case "quality_ready": return `🖼 کیفیت ${esc(n.label || "")} برای «${esc(n.title || "")}» کامل آماده شد`;
      case "quality_error": return `⚠️ آماده‌سازی کیفیت ${esc(n.label || "")} برای «${esc(n.title || "")}» با خطا مواجه شد`;
      case "subtitle_auto_extracted": return `💬 ${n.count || ""} زیرنویس از داخل «${esc(n.title || "")}» به‌صورت خودکار استخراج شد`;
      case "playlist_add_live": return `📡 <b>${name}</b> پخش زنده «${esc(n.title || "")}» را اضافه کرد`;
      case "playlist_remove": return `🗑 <b>${name}</b> «${esc(n.title || "")}» را حذف کرد`;
      case "subtitle_add": return `💬 <b>${name}</b> زیرنویس «${esc(n.label || "")}» را اضافه کرد`;
      case "audio_track_add": return `🎧 <b>${name}</b> کانال صدای «${esc(n.label || "")}» را اضافه کرد`;
      case "ctrl_play": return `▶️ <b>${name}</b> پخش را شروع کرد`;
      case "ctrl_pause": return `⏸ <b>${name}</b> پخش را مکث کرد`;
      case "ctrl_seek": return `⏩ <b>${name}</b> زمان را به ${formatTime(extra.to)} برد`;
      case "ctrl_rate": return `🚀 <b>${name}</b> سرعت پخش را ${extra.rate}x کرد`;
      case "ctrl_select": {
        const item = typeof extra.index === "number" ? live.playlist[extra.index] : null;
        return `🎬 <b>${name}</b> «${esc(item ? item.title : "یک ویدیو")}» را برای پخش انتخاب کرد`;
      }
      default: return `${name} یک تغییر اعمال کرد`;
    }
  }

  function pushNotify(n) {
    const li = document.createElement("li");
    li.innerHTML = `${notifyText(n)}<span class="nt-time">${new Date((n.ts || Date.now() / 1000) * 1000).toLocaleTimeString("fa-IR")}</span>`;
    notifyFeed.insertBefore(li, notifyFeed.firstChild);
    while (notifyFeed.children.length > 40) notifyFeed.removeChild(notifyFeed.lastChild);
  }

  function pushToast(n) {
    const t = document.createElement("div");
    t.className = "toast" + (n._warn ? " toast-warn" : "");
    t.innerHTML = notifyText(n);
    toastContainer.appendChild(t);
    setTimeout(() => {
      t.classList.add("out");
      setTimeout(() => t.remove(), 250);
    }, 4000);
  }

  // ===================================================================
  // چت
  // ===================================================================
  function updateChatBadge() {
    chatBadge.hidden = unreadChat === 0;
    chatBadge.textContent = unreadChat > 9 ? "9+" : String(unreadChat);
  }

  function chatMsgNode(m) {
    const li = document.createElement("li");
    li.className = "chat-msg" + (m.name === myName ? " self" : "");
    const av = avatarNode(m.name, m.avatar_url, "chat-msg-avatar");
    const body = document.createElement("div");
    body.className = "chat-msg-body";
    const head = document.createElement("div");
    head.className = "chat-msg-head";
    const nameEl = document.createElement("span");
    nameEl.className = "chat-msg-name";
    nameEl.textContent = m.name;
    const timeEl = document.createElement("span");
    timeEl.className = "chat-msg-time";
    timeEl.textContent = new Date((m.ts || Date.now() / 1000) * 1000).toLocaleTimeString("fa-IR");
    head.appendChild(nameEl); head.appendChild(timeEl);
    body.appendChild(head);
    if (m.text) {
      const textEl = document.createElement("div");
      textEl.className = "chat-msg-text";
      textEl.textContent = m.text;
      body.appendChild(textEl);
    }
    if (m.image_url) {
      const img = document.createElement("img");
      img.className = "chat-msg-img";
      img.src = m.image_url;
      img.alt = "";
      img.addEventListener("click", () => window.open(m.image_url, "_blank"));
      body.appendChild(img);
    }
    li.appendChild(av);
    li.appendChild(body);
    return li;
  }

  function renderChatHistory(messages) {
    chatFeed.innerHTML = "";
    chatEmptyHint.hidden = messages.length > 0;
    messages.forEach((m) => chatFeed.appendChild(chatMsgNode(m)));
    chatFeed.scrollTop = chatFeed.scrollHeight;
  }

  function appendChatMessage(m) {
    chatEmptyHint.hidden = true;
    chatFeed.appendChild(chatMsgNode(m));
    chatFeed.scrollTop = chatFeed.scrollHeight;
    if (!chatTabActive && m.name !== myName) {
      unreadChat++;
      updateChatBadge();
    }
  }

  btnChatImage.addEventListener("click", () => chatImageInput.click());
  chatImageInput.addEventListener("change", async () => {
    const file = chatImageInput.files[0];
    if (!file) return;
    const fd = new FormData();
    fd.append("file", file);
    try {
      const res = await fetch("/stream/api/chat/image", { method: "POST", body: fd });
      const data = await res.json();
      if (res.ok) {
        pendingChatImageUrl = data.url;
        chatImagePreviewImg.src = data.url;
        chatImagePreview.hidden = false;
      } else {
        alert("خطا در آپلود عکس: " + (data.error || ""));
      }
    } catch {
      alert("خطا در ارتباط با سرور");
    }
    chatImageInput.value = "";
  });
  btnChatImageRemove.addEventListener("click", () => {
    pendingChatImageUrl = null;
    chatImagePreview.hidden = true;
  });

  formChat.addEventListener("submit", (e) => {
    e.preventDefault();
    const text = chatTextInput.value.trim();
    if (!text && !pendingChatImageUrl) return;
    if (!socket) return;
    socket.emit("chat_send", { name: myName, text, image_url: pendingChatImageUrl });
    chatTextInput.value = "";
    pendingChatImageUrl = null;
    chatImagePreview.hidden = true;
  });

  btnClearChat.addEventListener("click", async () => {
    if (!confirm("کل چت برای همه پاک بشه؟ این کار قابل بازگشت نیست.")) return;
    try {
      await fetch(`/stream/api/chat/clear?name=${encodeURIComponent(myName)}`, { method: "POST" });
    } catch {
      alert("خطا در ارتباط با سرور");
    }
  });

  // ===================================================================
  // تب‌ها
  // ===================================================================
  document.querySelectorAll(".tab-btn").forEach((btn) => {
    btn.addEventListener("click", () => switchTab(btn.dataset.tab));
  });
  document.querySelectorAll("[data-go-tab]").forEach((el) => {
    el.addEventListener("click", () => switchTab(el.dataset.goTab));
  });
  function switchTab(name) {
    document.querySelectorAll(".tab-btn").forEach((b) => b.classList.toggle("active", b.dataset.tab === name));
    document.querySelectorAll(".tab-panel").forEach((p) => { p.hidden = p.dataset.panel !== name; });
    chatTabActive = name === "chat";
    if (chatTabActive) { unreadChat = 0; updateChatBadge(); chatFeed.scrollTop = chatFeed.scrollHeight; }
  }

  document.querySelectorAll(".mode-switch").forEach((switchEl) => {
    const panel = switchEl.closest(".tab-panel") || document;
    switchEl.querySelectorAll(".mode-btn").forEach((btn) => {
      btn.addEventListener("click", () => {
        switchEl.querySelectorAll(".mode-btn").forEach((b) => b.classList.toggle("active", b === btn));
        panel.querySelectorAll("[data-mode-panel]").forEach((form) => {
          form.hidden = form.dataset.modePanel !== btn.dataset.mode;
        });
      });
    });
  });

  // ===================================================================
  // فرم: آپلود فایل
  // ===================================================================
  $("formFile").addEventListener("submit", (e) => {
    e.preventDefault();
    const file = $("fileInput").files[0];
    if (!file) return;
    const fd = new FormData();
    fd.append("file", file);
    fd.append("title", $("fileTitle").value.trim());
    fd.append("name", myName);

    const track = $("uploadProgressTrack");
    const fill = $("uploadProgressFill");
    track.hidden = false; fill.style.width = "0%";

    const xhr = new XMLHttpRequest();
    xhr.open("POST", "/stream/api/upload");
    xhr.upload.addEventListener("progress", (ev) => {
      if (ev.lengthComputable) fill.style.width = ((ev.loaded / ev.total) * 100) + "%";
    });
    xhr.onload = () => {
      track.hidden = true;
      if (xhr.status >= 200 && xhr.status < 300) {
        $("formFile").reset();
      } else {
        alert("خطا در آپلود: " + xhr.responseText);
      }
    };
    xhr.onerror = () => { track.hidden = true; alert("خطا در ارتباط با سرور"); };
    xhr.send(fd);
  });

  // ===================================================================
  // فرم: افزودن لینک
  // ===================================================================
  $("formUrl").addEventListener("submit", async (e) => {
    e.preventDefault();
    const url = $("urlInput").value.trim();
    if (!url) return;
    const res = await fetch("/stream/api/add-url", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url, title: $("urlTitle").value.trim(), name: myName }),
    });
    if (res.ok) $("formUrl").reset();
    else alert("خطا: " + (await res.text()));
  });

  // ── YouTube quality picker ─────────────────────────────────────────
  let _ytCurrentUrl = "";

  async function ytSetStatus(msg, isError = false) {
    const el = $("ytStatus");
    el.hidden = false;
    el.className = "yt-status" + (isError ? " error" : "");
    el.textContent = msg;
  }
  function ytClearStatus() { $("ytStatus").hidden = true; }

  $("btnYtLoad").addEventListener("click", async () => {
    const url = $("youtubeInput").value.trim();
    if (!url) return;

    $("ytPreview").hidden = true;
    ytSetStatus("⏳ در حال دریافت اطلاعات ویدیو… (۱۵–۴۵ ثانیه)");
    $("btnYtLoad").disabled = true;

    try {
      const res = await fetch("/stream/api/youtube-formats", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ url }),
      });
      const data = await res.json();

      if (!res.ok) {
        ytSetStatus("❌ " + (data.error || "خطای نامشخص"), true);
        return;
      }

      const sel = $("ytQualitySelect");
      sel.innerHTML = "";
      (data.formats || []).forEach((f) => {
        const opt = document.createElement("option");
        opt.value = f.format_id;
        opt.textContent = f.label;
        sel.appendChild(opt);
      });

      $("ytPreviewTitle").textContent = data.title || "";
      $("youtubeTitle").value = "";
      const thumb = $("ytThumb");
      if (data.thumbnail) { thumb.src = data.thumbnail; thumb.hidden = false; }
      else { thumb.hidden = true; }

      _ytCurrentUrl = url;
      $("ytPreview").hidden = false;
      ytClearStatus();
    } catch {
      ytSetStatus("❌ خطا در ارتباط با سرور", true);
    } finally {
      $("btnYtLoad").disabled = false;
    }
  });

  $("formYoutube").addEventListener("submit", async (e) => {
    e.preventDefault();
    const url = _ytCurrentUrl || $("youtubeInput").value.trim();
    if (!url) return;

    const formatId = $("ytQualitySelect").value || "__best__";
    const btn = $("btnYoutubeSubmit");
    btn.disabled = true;

    try {
      const res = await fetch("/stream/api/add-youtube", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          url, format_id: formatId,
          title: $("youtubeTitle").value.trim() || $("ytPreviewTitle").textContent,
          name: myName,
        }),
      });
      const data = await res.json();
      if (res.ok) {
        ytSetStatus(`✅ «${data.title}» اضافه شد — دانلود و آماده‌سازی در پس‌زمینه ادامه داره`);
        $("formYoutube").reset();
        $("ytPreview").hidden = true;
        _ytCurrentUrl = "";
        setTimeout(ytClearStatus, 4000);
      } else {
        ytSetStatus("❌ " + (data.error || "خطای نامشخص"), true);
      }
    } catch {
      ytSetStatus("❌ خطا در ارتباط با سرور", true);
    }
    btn.disabled = false;
  });

  $("btnGenKey").addEventListener("click", async () => {
    const res = await fetch("/stream/api/live/new-key", { method: "POST" });
    generatedLive = await res.json();
    $("pushUrlOut").textContent = generatedLive.push_url;
    $("streamKeyOut").textContent = generatedLive.key;
    $("playbackUrlOut").textContent = generatedLive.playback_url;
    $("keyResult").hidden = false;
  });

  document.querySelectorAll(".copy-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      const text = $(btn.dataset.copy).textContent;
      navigator.clipboard.writeText(text).then(() => {
        const old = btn.textContent;
        btn.textContent = "کپی شد ✓";
        setTimeout(() => { btn.textContent = old; }, 1500);
      });
    });
  });

  $("btnAddGeneratedLive").addEventListener("click", async () => {
    if (!generatedLive) return;
    await fetch("/stream/api/add-live", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        title: "پخش زنده", playback_url: generatedLive.playback_url,
        key: generatedLive.key, name: myName,
      }),
    });
    $("keyResult").hidden = true;
    generatedLive = null;
  });

  $("formLive").addEventListener("submit", async (e) => {
    e.preventDefault();
    const playback_url = $("livePlayback").value.trim();
    if (!playback_url) return;
    const res = await fetch("/stream/api/add-live", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title: $("liveTitle").value.trim() || "پخش زنده", playback_url, name: myName }),
    });
    if (res.ok) $("formLive").reset();
    else alert("خطا: " + (await res.text()));
  });

  // ===================================================================
  // زیرنویس و کانال صدای خارجی
  // ===================================================================
  function populateSubTargetSelect() {
    const sel = $("subTargetSelect");
    const prevValue = sel.value;
    sel.innerHTML = "";
    if (!live.playlist.length) {
      const opt = document.createElement("option");
      opt.textContent = "ابتدا یک ویدیو به پلی‌لیست اضافه کن";
      opt.value = "";
      sel.appendChild(opt);
      sel.disabled = true;
      return;
    }
    sel.disabled = false;
    live.playlist.forEach((item, idx) => {
      const opt = document.createElement("option");
      opt.value = item.id;
      opt.textContent = (idx === live.current_index ? "▶ " : "") + item.title;
      sel.appendChild(opt);
    });
    const stillExists = [...sel.options].some((o) => o.value === prevValue);
    if (stillExists) sel.value = prevValue;
    else if (live.current_index !== null && live.playlist[live.current_index]) {
      sel.value = live.playlist[live.current_index].id;
    }
  }

  $("btnCleanup").addEventListener("click", async () => {
    const btn = $("btnCleanup");
    btn.disabled = true;
    cleanupStatus.textContent = "⏳ در حال پاکسازی…";
    try {
      const res = await fetch("/stream/api/cleanup", { method: "POST" });
      const data = await res.json();
      if (res.ok) {
        const total = data.total || 0;
        cleanupStatus.textContent = total > 0 ? `✅ ${total} فایل یتیم حذف شد` : "✅ هیچ فایل یتیمی پیدا نشد";
        dbg("PLAYER", "orphan cleanup done", data.removed);
      } else {
        cleanupStatus.textContent = "❌ خطا در پاکسازی";
      }
    } catch {
      cleanupStatus.textContent = "❌ خطا در ارتباط با سرور";
    }
    btn.disabled = false;
    setTimeout(() => { cleanupStatus.textContent = ""; }, 5000);
  });

  $("formSubFile").addEventListener("submit", async (e) => {
    e.preventDefault();
    const itemId = $("subTargetSelect").value;
    const file = $("subFileInput").files[0];
    if (!itemId) { alert("ابتدا یک ویدیوی مقصد انتخاب کن"); return; }
    if (!file) return;
    const fd = new FormData();
    fd.append("file", file);
    fd.append("item_id", itemId);
    fd.append("label", $("subFileLabel").value.trim() || "زیرنویس");
    fd.append("lang", "fa");
    fd.append("name", myName);
    const res = await fetch("/stream/api/subtitle-upload", { method: "POST", body: fd });
    if (res.ok) $("formSubFile").reset();
    else alert("خطا: " + (await res.text()));
  });

  $("formSubUrl").addEventListener("submit", async (e) => {
    e.preventDefault();
    const itemId = $("subTargetSelect").value;
    const url = $("subUrlInput").value.trim();
    if (!itemId) { alert("ابتدا یک ویدیوی مقصد انتخاب کن"); return; }
    if (!url) return;
    const res = await fetch("/stream/api/subtitle-url", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ item_id: itemId, url, label: $("subUrlLabel").value.trim() || "زیرنویس", lang: "fa", name: myName }),
    });
    if (res.ok) $("formSubUrl").reset();
    else alert("خطا: " + (await res.text()));
  });

  $("formAudioTrack").addEventListener("submit", async (e) => {
    e.preventDefault();
    const itemId = $("subTargetSelect").value;
    const url = $("audioUrlInput").value.trim();
    if (!itemId) { alert("ابتدا یک ویدیوی مقصد انتخاب کن"); return; }
    if (!url) return;
    const res = await fetch("/stream/api/audio-track", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ item_id: itemId, url, label: $("audioLabel").value.trim() || "دوبله", name: myName }),
    });
    if (res.ok) $("formAudioTrack").reset();
    else alert("خطا: " + (await res.text()));
  });

  // ===================================================================
  // هدر / فوتر مشترک
  // ===================================================================
  const yearEl = document.getElementById("year");
  if (yearEl) yearEl.textContent = new Date().getFullYear();

  const ham = document.getElementById("hamburger");
  const mob = document.getElementById("mobile-nav");
  ham?.addEventListener("click", () => { ham.classList.toggle("open"); mob.classList.toggle("open"); });
  mob?.querySelectorAll(".nav-link").forEach((l) => l.addEventListener("click", () => {
    ham.classList.remove("open"); mob.classList.remove("open");
  }));
  window.addEventListener("scroll", () => {
    document.getElementById("header")?.classList.toggle("scrolled", scrollY > 10);
  }, { passive: true });

  // ===================================================================
  // راه‌اندازی اولیه
  // ===================================================================
  initSubStylePanel();
  renderTransportUI();
  updateMuteIcon(1);
  renderSofa([]);
  // Chat history is also fetchable over REST in case the socket takes a
  // moment to connect (e.g. slow network) — this way the tab isn't empty.
  fetch("/stream/api/chat/history").then((r) => r.json()).then((d) => renderChatHistory(d.messages || [])).catch(() => {});
})();