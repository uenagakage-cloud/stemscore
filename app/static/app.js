"use strict";
const $ = (s) => document.querySelector(s);
const $$ = (s) => [...document.querySelectorAll(s)];

const STEM_INFO = {
  vocals: { label: "ボーカル", color: "#e84393" },
  lead:   { label: "メインボーカル", color: "#e84393" },
  backing:{ label: "コーラス", color: "#fd79a8" },
  drums:  { label: "ドラム",   color: "#f39c12" },
  bass:   { label: "ベース",   color: "#00b894" },
  guitar: { label: "ギター",   color: "#0984e3" },
  piano:  { label: "ピアノ",   color: "#8e44ad" },
  other:  { label: "その他",   color: "#7f8c8d" },
};
const STEM_ORDER = ["vocals", "lead", "backing", "drums", "bass", "guitar", "piano", "other"];
// メインボーカルを分離済みの曲では、ミキサーに「ボーカル」の代わりに「メインボーカル」「コーラス」を並べる
function mixerStems(job) {
  const all = Object.keys(job.stems || {});
  return all.filter((n) => !(n === "vocals" && all.includes("lead") && all.includes("backing")));
}
function leadName() { return Player.tracks.some((t) => t.name === "lead") ? "lead" : "vocals"; }
const DRUM_NAMES = { 36: "Kick", 38: "Snare", 42: "Hi-hat", 45: "Tom", 49: "Crash" };
const NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"];

const S = {
  jobs: [], job: null, src: "url", file: null,
  part: null, view: "score", transpose: 0, notes: {}, osmd: null, scoreReq: 0,
};

// ───────── util ─────────
function toast(msg, ms = 2600) {
  const t = $("#toast"); t.textContent = msg; t.hidden = false;
  clearTimeout(toast._t); toast._t = setTimeout(() => (t.hidden = true), ms);
}
function fmt(t, dec = false) {
  t = Math.max(0, t || 0);
  const m = Math.floor(t / 60), s = t - m * 60;
  return `${m}:${dec ? s.toFixed(1).padStart(4, "0") : String(Math.floor(s)).padStart(2, "0")}`;
}
function esc(s) { return String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c])); }
async function api(path, opt) {
  const r = await fetch(path, opt);
  if (r.status === 401) { location.reload(); throw new Error("アクセスキーが必要です"); }
  if (!r.ok) {
    let msg = r.statusText;
    try { msg = (await r.json()).detail || msg; } catch {}
    throw new Error(msg);
  }
  return r.headers.get("content-type")?.includes("json") ? r.json() : r.text();
}
const store = {
  get(k, d) { try { return JSON.parse(localStorage.getItem(k)) ?? d; } catch { return d; } },
  set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch {} },
};
function cssVar(n) { return getComputedStyle(document.documentElement).getPropertyValue(n).trim(); }
function midiName(n) { return NOTE_NAMES[n % 12] + (Math.floor(n / 12) - 1); }

// ───────── theme / help ─────────
(function initTheme() {
  const t = store.get("theme", null);
  if (t) document.documentElement.dataset.theme = t;
  $("#themeBtn").onclick = () => {
    const dark = matchMedia("(prefers-color-scheme: dark)").matches;
    const cur = document.documentElement.dataset.theme || (dark ? "dark" : "light");
    const next = cur === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = next; store.set("theme", next);
    Player.redrawAll();
  };
  $("#helpBtn").onclick = () => $("#helpDlg").showModal();
})();

// ───────── 新規ジョブフォーム ─────────
$$("#srcSeg button").forEach((b) => (b.onclick = () => {
  S.src = b.dataset.src;
  $$("#srcSeg button").forEach((x) => x.classList.toggle("on", x === b));
  $("#urlBox").hidden = S.src !== "url"; $("#fileBox").hidden = S.src !== "file";
  $("#inboxBox").hidden = S.src !== "inbox";
  if (S.src === "inbox") loadInbox();
}));

// ───────── 曲フォルダ (Google ドライブの「曲」) ─────────
async function loadInbox() {
  const ul = $("#inboxList");
  ul.innerHTML = `<li class="hint">読み込み中…</li>`;
  try {
    const items = await api("/api/inbox");
    if (!items.length) { ul.innerHTML = `<li class="hint">ファイルがありません。ドライブの「曲」フォルダに入れてください</li>`; return; }
    ul.innerHTML = items.map((f) => `<li data-name="${esc(f.name)}" class="${S.inbox === f.name ? "on" : ""}">${esc(f.name)}
      <small>${(f.size / 1048576).toFixed(1)} MB · ${new Date(f.mtime * 1000).toLocaleString("ja-JP")}</small></li>`).join("");
    ul.querySelectorAll("li[data-name]").forEach((li) => (li.onclick = () => {
      S.inbox = li.dataset.name;
      ul.querySelectorAll("li").forEach((x) => x.classList.toggle("on", x === li));
    }));
  } catch (e) { ul.innerHTML = `<li class="hint">読み込めませんでした: ${esc(e.message)}</li>`; }
}
$("#inboxReload").onclick = loadInbox;
$("#sensitivity").oninput = (e) => ($("#sensOut").textContent = (+e.target.value).toFixed(2));
const drop = $("#drop");
function setFile(f) { S.file = f; $("#dropText").innerHTML = f ? `<b>${esc(f.name)}</b><br><small>${(f.size / 1048576).toFixed(1)} MB</small>` : "ここに音声/動画ファイルをドロップ"; }
$("#fileInput").onchange = (e) => setFile(e.target.files[0]);
["dragenter", "dragover"].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.add("over"); }));
["dragleave", "drop"].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.remove("over"); }));
drop.addEventListener("drop", (e) => e.dataTransfer.files[0] && setFile(e.dataTransfer.files[0]));
// ページ全体へのドロップでもファイル指定
document.addEventListener("dragover", (e) => e.preventDefault());
document.addEventListener("drop", (e) => {
  e.preventDefault();
  const f = e.dataTransfer?.files?.[0];
  if (f) { $$("#srcSeg button")[1].click(); setFile(f); }
});

$("#jobForm").onsubmit = async (e) => {
  e.preventDefault();
  const fd = new FormData();
  if (S.src === "url") {
    const u = $("#urlInput").value.trim();
    if (!u) return toast("URL を入力してください");
    fd.append("url", u);
  } else if (S.src === "inbox") {
    if (!S.inbox) return toast("ファイルを選んでください");
    fd.append("inbox", S.inbox);
  } else {
    if (!S.file) return toast("ファイルを選択してください");
    const lim = S.cfg?.upload_limit_mb;
    if (lim && S.file.size > lim * 1048576) {
      return toast(`${lim}MB を超えるファイルは直接送れません。Google ドライブの「曲」フォルダに入れて「Drive」タブから選んでください`, 7000);
    }
    fd.append("file", S.file);
  }
  fd.append("model", $("#model").value);
  fd.append("transcribe", $("#transcribe").checked);
  fd.append("sensitivity", $("#sensitivity").value);
  fd.append("shifts", $("#shifts").value);
  fd.append("split_vocals", $("#splitVocals").checked);
  fd.append("split_quality", $("#splitQuality").value);
  if ($("#trimStart").value) fd.append("start", $("#trimStart").value);
  if ($("#trimEnd").value) fd.append("end", $("#trimEnd").value);
  const btn = $("#submitBtn"); btn.disabled = true; btn.textContent = "送信中…";
  try {
    const job = await uploadForm(fd);
    $("#urlInput").value = ""; setFile(null);
    await refreshList();
    openJob(job.id);
    toast("処理を開始しました");
  } catch (err) { toast("エラー: " + err.message, 5000); }
  finally { btn.disabled = false; btn.textContent = "分離と採譜を開始"; }
};

// XHR で送信してアップロードの進み具合を表示 (スマホから大きな動画を送る場合など)
function uploadForm(fd) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    const box = $("#uploadBox"), hasFile = S.src === "file" && S.file;
    box.hidden = !hasFile;
    xhr.upload.onprogress = (e) => {
      if (!e.lengthComputable) return;
      const p = e.loaded / e.total;
      $("#upBar").style.width = `${(p * 100).toFixed(1)}%`;
      $("#upText").textContent = p < 1
        ? `アップロード中… ${(e.loaded / 1048576).toFixed(0)} / ${(e.total / 1048576).toFixed(0)} MB`
        : "サーバーで受け取り中…";
    };
    xhr.onload = () => {
      box.hidden = true;
      if (xhr.status === 401) { location.reload(); return; }
      let data = null; try { data = JSON.parse(xhr.responseText); } catch {}
      if (xhr.status >= 200 && xhr.status < 300) resolve(data);
      else reject(new Error(data?.detail || xhr.statusText || "送信に失敗しました"));
    };
    xhr.onerror = () => { box.hidden = true; reject(new Error("通信エラー")); };
    xhr.open("POST", "/api/jobs");
    xhr.send(fd);
  });
}

// ───────── スマホ用タブ ─────────
const isMobile = () => matchMedia("(max-width: 900px)").matches;
function setTab(tab) {
  document.body.dataset.tab = tab;
  $$("#mobnav button").forEach((b) => b.classList.toggle("on", b.dataset.tab === tab));
  if (tab === "song") requestAnimationFrame(() => Player.redrawAll());
  scrollTo({ top: 0 });
}
$$("#mobnav button").forEach((b) => (b.onclick = () => setTab(b.dataset.tab)));

// ───────── 分離モデルの状態と取り込み ─────────
async function refreshModels(st) {
  const models = st || await api("/api/models").catch(() => null);
  if (!models) return;
  const sel = $("#model");
  [...sel.options].forEach((o) => {
    const m = models[o.value]; if (!m) return;
    o.textContent = o.textContent.replace(/（未取得）$/, "") + (m.available ? "" : "（未取得）");
  });
  const missing = Object.entries(models).filter(([, m]) => !m.available);
  $("#modelSummary").textContent = missing.length ? `${missing.length} 個が未取得` : "すべて利用可能";
  $("#modelList").innerHTML = Object.entries(models).map(([k, m]) =>
    `<div>${m.available ? '<span class="ok">✔</span>' : '<span class="ng">✖</span>'} ${esc(m.label)}${m.available ? "" : `（${m.missing.length} ファイル不足）`}</div>`).join("");
}
$("#modelImportBtn").onclick = async () => {
  const fd = new FormData();
  fd.append("links", $("#modelLinks").value);
  for (const f of $("#modelFiles").files) fd.append("files", f);
  const btn = $("#modelImportBtn"); btn.disabled = true;
  const box = $("#modelLog"); box.hidden = false; box.textContent = "送信中…";
  try {
    await api("/api/models/import", { method: "POST", body: fd });
    for (;;) {
      await new Promise((r) => setTimeout(r, 1500));
      const st = await api("/api/models/import");
      box.textContent = st.log.join("\n");
      refreshModels(st.models);
      if (!st.running) { toast(st.error ? "取り込みに失敗しました" : "モデルを取り込みました"); break; }
    }
    $("#modelLinks").value = ""; $("#modelFiles").value = "";
  } catch (e) { box.textContent = "エラー: " + e.message; }
  finally { btn.disabled = false; }
};
$("#model").addEventListener("change", () => {
  if ($("#model").selectedOptions[0].textContent.endsWith("（未取得）")) {
    toast("このモデルはまだ PC にありません。「分離モデルの追加」から取り込めます", 4500);
    $("#modelDetails").open = true;
  }
});

// ───────── 履歴 ─────────
async function refreshList() {
  try { S.jobs = await api("/api/jobs"); } catch { return; }
  const ul = $("#jobList");
  if (!S.jobs.length) { ul.innerHTML = `<li class="hint">まだありません</li>`; return; }
  ul.innerHTML = S.jobs.map((j) => {
    const busy = j.status === "running" || j.status === "queued";
    const stage = j.status === "queued" && j.queue_pos ? `順番待ち（${j.queue_pos} 番目）` : (j.stage || j.status);
    const meta = (j.status === "done" ? `${j.key ?? ""} · ${j.bpm ?? ""} BPM` : esc(stage))
      + (S.me?.admin && j.owner !== "admin" ? ` <span class="owner-tag">${esc(j.owner_name || "メンバー")}</span>` : "");
    return `<li data-id="${j.id}" class="${S.job?.id === j.id ? "active" : ""}">
      <div class="t" title="${esc(j.title)}">${esc(j.title)}</div>
      <div class="m"><span class="dot ${j.status}"></span>${meta}</div>
      ${busy ? `<div class="mini-bar"><div style="width:${(j.progress * 100).toFixed(0)}%"></div></div>` : ""}
    </li>`;
  }).join("");
  ul.querySelectorAll("li[data-id]").forEach((li) => (li.onclick = () => openJob(li.dataset.id)));
}

// ───────── ジョブ詳細 ─────────
let pollTimer = null;
async function openJob(id) {
  clearTimeout(pollTimer);
  const seq = (S.openSeq = (S.openSeq || 0) + 1);   // 続けて開いたときは最後に開いた曲だけを表示する
  const job = await api(`/api/jobs/${id}`);
  if (seq !== S.openSeq) return;
  Player.unload();
  S.notes = {}; S.part = null;
  S.job = job;
  store.set("lastJob", id);
  $("#empty").hidden = true; $("#detail").hidden = false;
  if (isMobile() && !openJob.silent) setTab("song");
  renderHead();
  $$("#jobList li").forEach((li) => li.classList.toggle("active", li.dataset.id === id));
  if (job.status === "done") buildDone();
  else { $("#playerCard").hidden = true; $("#partsCard").hidden = true; poll(); }
}

async function poll() {
  if (!S.job) return;
  const id = S.job.id;
  try {
    const job = await api(`/api/jobs/${id}`);
    if (S.job?.id !== id) return;
    const was = S.job.status;
    S.job = job;
    renderHead();
    if (job.status === "done" && was !== "done") { buildDone(); refreshList(); toast("完了しました 🎉"); }
    if (job.status === "running" || job.status === "queued") pollTimer = setTimeout(poll, 1500);
    else refreshList();
  } catch { pollTimer = setTimeout(poll, 3000); }
}

function renderHead() {
  const j = S.job, a = j.analysis;
  $("#jobTitle").textContent = j.title;
  const chips = [];
  if (a) {
    chips.push(`<span class="chip">キー <b>${esc(a.key.name)}</b>${S.pitch ? ` → <b>${esc(keyLabel())}</b>` : ""}</span>`);
    chips.push(`<span class="chip">BPM <b>${a.bpm}</b></span>`);
    chips.push(`<span class="chip">長さ ${fmt(a.duration)}</span>`);
    const alt = a.key_candidates.slice(1, 3).map((k) => k.name).join(" / ");
    if (alt) chips.push(`<span class="chip" title="キー推定の他の候補">候補: ${esc(alt)}</span>`);
  }
  chips.push(`<span class="chip">${esc(j.options.model)}</span>`);
  if (j.source.type === "url") chips.push(`<a class="chip" href="${esc(j.source.url)}" target="_blank" rel="noopener">元のURL ↗</a>`);
  $("#chips").innerHTML = chips.join("");
  const busy = j.status === "running" || j.status === "queued";
  $("#progressBox").hidden = j.status === "done";
  $("#progBar").style.width = `${(j.progress * 100).toFixed(1)}%`;
  $("#progStage").textContent = j.status === "error" ? `エラー: ${j.error}`
    : j.status === "queued" && j.queue_pos ? `順番待ち（${j.queue_pos} 番目）。前の曲が終わると自動で始まります` : j.stage;
  $("#progPct").textContent = busy ? `${(j.progress * 100).toFixed(0)}%` : "";
  $("#cancelBtn").hidden = !busy; $("#retryBtn").hidden = busy;
  const lb = $("#logBox"); const atBottom = lb.scrollTop + lb.clientHeight >= lb.scrollHeight - 4;
  lb.textContent = (j.log || []).join("\n");
  if (atBottom) lb.scrollTop = lb.scrollHeight;
  $("#zipBtn").href = `/api/jobs/${j.id}/zip`;
  $("#zipBtn").hidden = j.status !== "done";
  $("#splitBtn").hidden = !(j.status === "done" && j.stems?.vocals && !j.stems?.lead && S.cfg?.karaoke?.installed);
}

$("#delBtn").onclick = async () => {
  if (!S.job || !confirm(`「${S.job.title}」を削除しますか？`)) return;
  await api(`/api/jobs/${S.job.id}`, { method: "DELETE" });
  Player.unload(); S.job = null;
  $("#detail").hidden = true; $("#empty").hidden = false;
  refreshList();
};
$("#cancelBtn").onclick = async () => { await api(`/api/jobs/${S.job.id}/cancel`, { method: "POST" }); toast("中止を要求しました"); };
$("#retryBtn").onclick = async () => {
  try { await api(`/api/jobs/${S.job.id}/retry`, { method: "POST" }); S.job.status = "queued"; poll(); refreshList(); }
  catch (e) { toast(e.message); }
};

function buildDone() {
  $("#playerCard").hidden = false;
  // 曲ごとのキー: この端末で選んだもの → サーバーに保存されたもの の順に使う
  S.pitch = store.get(`pitch:${S.job.id}`, S.job.pitch ?? 0);
  Player.load(S.job);
  updateKeyUI();
  renderHead();
  setTranspose(S.pitch);   // 楽譜の移調も曲のキーに合わせる
  const parts = Object.keys(S.job.parts || {}).sort((a, b) => STEM_ORDER.indexOf(a) - STEM_ORDER.indexOf(b));
  $("#partsCard").hidden = !parts.length;
  if (!parts.length) return;
  const tabs = parts.map((p) => `<button data-part="${p}">${STEM_INFO[p]?.label ?? p}</button>`);
  if (parts.length > 1) tabs.push(`<button data-part="__full">総譜（全パート）</button>`);
  $("#partTabs").innerHTML = tabs.join("");
  $$("#partTabs button").forEach((b) => (b.onclick = () => selectPart(b.dataset.part)));
  selectPart(parts.includes("vocals") ? "vocals" : parts[0]);
}

// ───────── プレイヤー (分離音源のミキサー) ─────────
const Player = {
  ctx: null, master: null, tracks: [], dur: 0, playing: false, loop: false, a: null, b: null,
  click: false, schedCursor: 0, raf: 0, job: null,

  ensureCtx() {
    if (!this.ctx) {
      this.ctx = new (window.AudioContext || window.webkitAudioContext)();
      this.master = this.ctx.createGain();
      this.master.gain.value = +$("#master").value;
      this.master.connect(this.ctx.destination);
    }
    return this.ctx;
  },

  unload() {
    this.pause();
    this.tracks.forEach((t) => { t.audio.pause(); t.audio.src = ""; t.node?.disconnect(); });
    this.tracks = []; this.job = null; this.a = this.b = null;
    this.loop = false; $("#loopBtn").classList.remove("on"); $("#loopBtn").textContent = "⟲ ループ";
    $("#tracks").innerHTML = "";
    $("#miniPlayer").hidden = true; document.body.classList.remove("has-player");
  },

  load(job) {
    this.unload();
    this.job = job;
    $("#miniTitle").textContent = job.title;
    $("#miniPlayer").hidden = false; document.body.classList.add("has-player");
    this.dur = job.analysis?.duration || 0;
    const names = mixerStems(job).sort((a, b) => STEM_ORDER.indexOf(a) - STEM_ORDER.indexOf(b));
    const box = $("#tracks");
    names.forEach((name, i) => {
      const st = job.stems[name], info = STEM_INFO[name] || { label: name, color: "#888" };
      const el = document.createElement("div");
      el.className = "track";
      const pk = job.parts?.[name];
      const extra = pk && name !== "drums" && pk.stats?.count
        ? `${pk.key ? "キー " + pk.key + " · " : ""}音域 ${pk.stats.low}–${pk.stats.high}` : "";
      el.innerHTML = `
        <div class="ctl">
          <div class="name"><i style="background:${info.color}"></i>${info.label} <span class="key">${i + 1}</span></div>
          <div class="btns">
            <button class="m" title="ミュート">M</button><button class="s" title="ソロ">S</button>
            <input type="range" min="0" max="1.5" step="0.01" value="1" title="音量">
            <a class="dl" data-file="${st.file}" href="/api/jobs/${job.id}/file/${st.file}?download=1" title="WAV をダウンロード (今のキー)">⬇WAV</a>
          </div>
          <div class="key">${esc(extra)}</div>
        </div>
        <canvas></canvas>`;
      box.appendChild(el);
      const audio = new Audio();
      audio.preload = "auto";
      audio.src = streamUrl(job.id, name);
      audio.preservesPitch = true; audio.webkitPreservesPitch = true;
      audio.playsInline = true;
      const t = { name, el, audio, peaks: st.peaks, color: info.color, vol: 1, mute: false, solo: false,
        canvas: el.querySelector("canvas"), node: null, gain: null };
      el.querySelector(".m").onclick = () => { t.mute = !t.mute; this.applyGains(); };
      el.querySelector(".s").onclick = () => { t.solo = !t.solo; this.applyGains(); };
      el.querySelector("input").oninput = (e) => { t.vol = +e.target.value; this.applyGains(); };
      this.bindSeek(t.canvas);
      this.tracks.push(t);
    });
    audioEnded(this.tracks[0]?.audio);
    this.applyGains();
    this.redrawAll();
    this.updateTime();
  },

  connect() {
    const ctx = this.ensureCtx();
    this.tracks.forEach((t) => {
      if (t.node) return;
      t.node = ctx.createMediaElementSource(t.audio);
      t.gain = ctx.createGain();
      t.node.connect(t.gain).connect(this.master);
    });
    this.applyGains();
  },

  applyGains() {
    const anySolo = this.tracks.some((t) => t.solo);
    this.tracks.forEach((t) => {
      const audible = !t.mute && (!anySolo || t.solo);
      const v = audible ? t.vol : 0;
      if (t.gain) t.gain.gain.setTargetAtTime(v, this.ctx.currentTime, 0.015);
      else t.audio.volume = Math.min(1, v);
      t.el.classList.toggle("muted", !audible);
      t.el.querySelector(".m").classList.toggle("on", t.mute);
      t.el.querySelector(".s").classList.toggle("on", t.solo);
    });
    const voc = this.tracks.find((t) => t.name === leadName());
    $("#karaokeBtn").classList.toggle("on", !!voc?.mute);
  },

  get time() { return this.tracks[0]?.audio.currentTime || 0; },

  async play() {
    if (!this.tracks.length) return;
    // iPhone/iPad ではタップ処理の中で同期的に再生を始めないとブロックされるため、await より前に play() する
    this.connect();
    const resumed = this.ctx.resume();
    const t = this.time;
    if (this.loop && this.a != null && (t < this.a || t >= this.b)) this.seek(this.a);
    else if (t >= this.dur - 0.05) this.seek(0);
    const rate = +$("#rate").value;
    const plays = this.tracks.map((tr) => { tr.audio.playbackRate = rate; return tr.audio.play().catch(() => {}); });
    this.playing = true;
    this.setPlayIcons();
    await Promise.all([resumed, ...plays]);
    this.schedCursor = this.time;
    cancelAnimationFrame(this.raf); this.tick();
  },

  setPlayIcons() {
    const icon = this.playing ? "❚❚" : "▶";
    $("#playBtn").textContent = icon; $("#miniPlay").textContent = icon; $("#scorePlay").textContent = icon;
  },

  pause() {
    this.tracks.forEach((t) => t.audio.pause());
    this.playing = false;
    this.setPlayIcons();
    Synth.stopAll();
  },

  seek(t) {
    t = Math.max(0, Math.min(this.dur, t));
    this.tracks.forEach((tr) => (tr.audio.currentTime = t));
    this.schedCursor = t;
    Synth.stopAll();
    this.updateTime(t);
  },

  setRate(r) {
    $("#rate").value = r; $("#rateOut").textContent = `${Math.round(r * 100)}%`;
    this.tracks.forEach((t) => (t.audio.playbackRate = r));
  },

  tick() {
    const t = this.time;
    // 各トラックのずれを補正
    if (this.playing) {
      for (const tr of this.tracks.slice(1)) {
        if (Math.abs(tr.audio.currentTime - t) > 0.05) tr.audio.currentTime = t;
      }
      if (this.loop && this.a != null && t >= this.b) this.seek(this.a);
      Scheduler.run(t);
    }
    this.updateTime(t);
    if (this.playing) this.raf = requestAnimationFrame(() => this.tick());
  },

  updateTime(t = this.time) {
    $("#timeLbl").textContent = `${fmt(t, true)} / ${fmt(this.dur)}`;
    $("#miniTime").textContent = `${fmt(t)} / ${fmt(this.dur)}`;
    this.tracks.forEach((tr) => this.drawWave(tr, t));
    drawChordLane(t);
    updateChordNow(t);
    if (S.view === "roll" && !$("#partsCard").hidden) Roll.draw(t);
    if (S.view === "score" && !$("#partsCard").hidden) ScoreCursor.update(t);
    $("#scoreTime").textContent = `${fmt(t)} / ${fmt(this.dur)}`;
  },

  redrawAll() { this.tracks.forEach((tr) => { tr.base = null; }); this.updateTime(); },

  drawWave(tr, t) {
    const c = tr.canvas, dpr = devicePixelRatio || 1;
    const w = c.clientWidth, h = c.clientHeight;
    if (!w) return;
    if (c.width !== Math.round(w * dpr) || !tr.base) {
      c.width = Math.round(w * dpr); c.height = Math.round(h * dpr);
      tr.base = [cssVar("--wave"), tr.color].map((col) => renderPeaks(tr.peaks, c.width, c.height, col));
    }
    const g = c.getContext("2d");
    g.clearRect(0, 0, c.width, c.height);
    const x = this.dur ? (t / this.dur) * c.width : 0;
    g.drawImage(tr.base[0], 0, 0);
    g.drawImage(tr.base[1], 0, 0, x, c.height, 0, 0, x, c.height);
    if (this.a != null) {
      const ax = (this.a / this.dur) * c.width, bx = (this.b / this.dur) * c.width;
      g.fillStyle = this.loop ? "rgba(240,180,41,.22)" : "rgba(127,127,127,.15)";
      g.fillRect(ax, 0, bx - ax, c.height);
    }
    g.fillStyle = cssVar("--text"); g.fillRect(x, 0, Math.max(1, dpr), c.height);
  },

  bindSeek(canvas) {
    let dragStart = null;
    const toT = (e) => {
      const r = canvas.getBoundingClientRect();
      return Math.max(0, Math.min(1, (e.clientX - r.left) / r.width)) * this.dur;
    };
    canvas.addEventListener("pointerdown", (e) => {
      if (e.shiftKey) { dragStart = toT(e); canvas.setPointerCapture(e.pointerId); }
      else this.seek(toT(e));
    });
    canvas.addEventListener("pointermove", (e) => {
      if (dragStart == null) return;
      const t = toT(e);
      this.a = Math.min(dragStart, t); this.b = Math.max(dragStart, t);
      this.updateTime();
    });
    canvas.addEventListener("pointerup", () => {
      if (dragStart == null) return;
      dragStart = null;
      if (this.b - this.a > 0.3) { this.setLoop(true); this.seek(this.a); }
    });
  },

  setLoop(on) {
    if (on && this.a == null) {
      // 区間未指定なら現在位置から 4 小節
      const beat = 60 / (this.job?.analysis?.bpm || 120);
      this.a = this.time; this.b = Math.min(this.dur, this.a + beat * 16);
    }
    this.loop = on;
    $("#loopBtn").classList.toggle("on", on);
    $("#loopBtn").textContent = on ? `⟲ ${fmt(this.a, true)}–${fmt(this.b, true)}` : "⟲ ループ";
    this.updateTime();
  },
};

function audioEnded(a) {
  if (a) a.onended = () => { if (!Player.loop) Player.pause(); };
}

function renderPeaks(peaks, w, h, color) {
  const c = document.createElement("canvas"); c.width = w; c.height = h;
  const g = c.getContext("2d"); g.fillStyle = color;
  const n = peaks.length || 1, mid = h / 2;
  const max = Math.max(0.05, ...peaks);
  for (let x = 0; x < w; x++) {
    const v = peaks[Math.floor((x / w) * n)] / max || 0;
    const hh = Math.max(1, v * mid * 0.95);
    g.fillRect(x, mid - hh, 1, hh * 2);
  }
  return c;
}

$("#playBtn").onclick = () => (Player.playing ? Player.pause() : Player.play());
$("#stopBtn").onclick = () => Player.seek(Player.loop && Player.a != null ? Player.a : 0);
$("#rate").oninput = (e) => Player.setRate(+e.target.value);
$("#master").oninput = (e) => { if (Player.master) Player.master.gain.value = +e.target.value; };
$("#loopBtn").onclick = () => Player.setLoop(!Player.loop);
$("#miniPlay").onclick = () => $("#playBtn").click();
// タッチ操作用: 現在位置を A / B 点に設定
$("#aBtn").onclick = () => {
  const t = Player.time;
  Player.a = t; if (Player.b == null || Player.b <= t + 0.3) Player.b = Math.min(Player.dur, t + 60 / (S.job?.analysis?.bpm || 120) * 16);
  Player.setLoop(Player.loop); toast(`A: ${fmt(t, true)}`);
};
$("#bBtn").onclick = () => {
  const t = Player.time;
  if (Player.a == null || t <= Player.a + 0.3) return toast("先に A 点を、B より前の位置で設定してください");
  Player.b = t; Player.setLoop(true); Player.seek(Player.a);
};
$("#clickBtn").onclick = () => { Player.click = !Player.click; $("#clickBtn").classList.toggle("on", Player.click); Player.schedCursor = Player.time; };
// カラオケ: メインボーカルだけを消す (分離前の曲ではボーカル全体)
$("#karaokeBtn").onclick = () => {
  const v = Player.tracks.find((t) => t.name === leadName());
  if (v) { v.mute = !v.mute; Player.applyGains(); }
};
$("#splitBtn").onclick = async () => {
  // 品質は「新しい曲を解析」の詳細設定にある「カラオケ分離の品質」と同じものを使う
  const q = $("#splitQuality").value;
  const label = q === "high" ? "高品質（3分の曲で約50分）" : "標準（3分の曲で約20分）";
  if (!confirm(`ボーカルをメインボーカルとコーラスに分けて、コーラス入りのカラオケ音源を作ります。\n品質: ${label}\n（品質は「詳細設定」の「カラオケ分離の品質」で変えられます）\n実行しますか？`)) return;
  try { await api(`/api/jobs/${S.job.id}/split_vocals?quality=${q}`, { method: "POST" }); openJob(S.job.id); refreshList(); }
  catch (e) { toast(e.message, 5000); }
};
addEventListener("resize", () => Player.redrawAll());

// ───────── 曲全体のキー (半音単位で上げ下げ。テンポはそのまま) ─────────
S.pitch = 0;
const SHARP = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"];
const FLAT = ["C", "Db", "D", "Eb", "E", "F", "Gb", "G", "Ab", "A", "Bb", "B"];
function streamUrl(jid, name) { return `/api/jobs/${jid}/stream/${name}${S.pitch ? `?pitch=${S.pitch}` : ""}`; }
// 移調後のキーでフラット系の表記を使うか (サーバーの analysis.py と同じ規則)
function useFlats() {
  const k = S.job?.analysis?.key; if (!k) return false;
  const t = (((k.tonic + S.pitch) % 12) + 12) % 12;
  return k.mode === "major" ? [5, 10, 3, 8, 1].includes(t) : [2, 7, 0, 5, 10].includes(t);
}
function transposeName(root, n) {
  const i = SHARP.indexOf(root) >= 0 ? SHARP.indexOf(root) : FLAT.indexOf(root);
  if (i < 0) return root;
  return (useFlats() ? FLAT : SHARP)[(((i + n) % 12) + 12) % 12];
}
function chordLabel(c) {
  if (!c || c === "N" || !S.pitch) return c;
  const root = c.length > 1 && "#b".includes(c[1]) ? c.slice(0, 2) : c[0];
  return transposeName(root, S.pitch) + c.slice(root.length);
}
function keyLabel() {
  const k = S.job?.analysis?.key; if (!k) return "";
  const [root, mode] = k.name.split(" ");
  return `${transposeName(root, S.pitch)} ${mode}`;
}
function updateKeyUI() {
  $("#kOut").textContent = (S.pitch > 0 ? "+" : "") + S.pitch;
  $("#keyName").textContent = keyLabel();
  if (!S.job) return;
  const q = S.pitch ? `&pitch=${S.pitch}` : "";
  $$(".track a.dl").forEach((a) => (a.href = `/api/jobs/${S.job.id}/file/${a.dataset.file}?download=1${q}`));
  // メインボーカル分離済みならコーラス入りのカラオケ音源、まだならボーカルを全部除いた伴奏
  const kara = S.job.stems?.lead ? "karaoke" : "instrumental";
  const c = $("#countIn").checked ? "&count=1" : "";
  $("#karaokeDl").href = `/api/jobs/${S.job.id}/file/${kara}.wav?download=1${q}${c}`;
  const voice = S.job.stems?.lead ? "lead" : "vocals";
  $("#vocalDl").href = `/api/jobs/${S.job.id}/file/${voice}.wav?download=1${q}${c}`;
  $("#vocalDl").textContent = S.job.stems?.lead ? "⬇ メインボーカル" : "⬇ ボーカル";
  $("#karaokeDl").textContent = S.job.stems?.lead ? "⬇ カラオケ音源（コーラス入り）" : "⬇ 伴奏（ボーカルなし）";
}
async function setPitch(n) {
  n = Math.max(-6, Math.min(6, n));
  if (!S.job || n === S.pitch) return;
  const ctl = $(".key-ctl"); ctl.classList.add("busy");
  const wasPlaying = Player.playing, t = Player.time;
  Player.pause();
  S.pitch = n;
  store.set(`pitch:${S.job.id}`, n);
  api(`/api/jobs/${S.job.id}/pitch?value=${n}`, { method: "POST" }).catch(() => {});
  updateKeyUI();
  renderHead();
  setTranspose(n);   // 楽譜も同じだけ移調する
  toast(n ? `キーを ${n > 0 ? "+" : ""}${n} に変更中…（初回は少し時間がかかります）` : "原曲のキーに戻しています…", 8000);
  // 全パートの音声を新しいキーのものに差し替え、読み込めたら同じ位置から再開
  await Promise.all(Player.tracks.map((tr) => new Promise((res) => {
    const done = () => { tr.audio.removeEventListener("canplay", done); tr.audio.removeEventListener("error", done); res(); };
    tr.audio.addEventListener("canplay", done); tr.audio.addEventListener("error", done);
    tr.audio.src = streamUrl(S.job.id, tr.name);
    tr.audio.load();
  })));
  Player.seek(t);
  ctl.classList.remove("busy");
  toast(`キー: ${keyLabel()}${n ? `（${n > 0 ? "+" : ""}${n}）` : "（原曲）"}`);
  if (wasPlaying) Player.play();
}
$("#kUp").onclick = () => setPitch(S.pitch + 1);
$("#countIn").onchange = () => { store.set("countIn", $("#countIn").checked); updateKeyUI(); };
$("#kDown").onclick = () => setPitch(S.pitch - 1);

// ───────── コード表示 ─────────
function chordsOf() { return S.job?.analysis?.chords || []; }
function chordAt(t) { return chordsOf().findIndex((c) => t >= c.start && t < c.end); }
function updateChordNow(t) {
  const cs = chordsOf(); const i = chordAt(t);
  $("#chordNow").textContent = i >= 0 && cs[i].chord !== "N" ? chordLabel(cs[i].chord) : "–";
  $("#miniChord").textContent = $("#chordNow").textContent;
  const nx = cs.slice(i + 1).find((c) => c.chord !== "N");
  $("#chordNext").textContent = nx ? chordLabel(nx.chord) : "–";
}
function drawChordLane(t) {
  const c = $("#chordLane"), dpr = devicePixelRatio || 1;
  // 波形の列と揃える
  const first = Player.tracks[0]?.canvas;
  if (!first) return;
  const wrap = $("#timelineWrap").getBoundingClientRect(), fr = first.getBoundingClientRect();
  c.style.marginLeft = `${fr.left - wrap.left}px`; c.style.width = `${fr.width}px`;
  const w = Math.round(fr.width * dpr), h = Math.round(26 * dpr);
  if (c.width !== w) { c.width = w; c.height = h; }
  const g = c.getContext("2d"); g.clearRect(0, 0, w, h);
  const dur = Player.dur || 1;
  g.font = `${11 * dpr}px sans-serif`; g.textBaseline = "middle";
  for (const ch of chordsOf()) {
    if (ch.chord === "N") continue;
    const x0 = (ch.start / dur) * w, x1 = (ch.end / dur) * w;
    const on = t >= ch.start && t < ch.end;
    g.fillStyle = on ? cssVar("--accent") : cssVar("--panel-2");
    g.fillRect(x0 + 0.5, 2 * dpr, Math.max(1, x1 - x0 - 1), h - 4 * dpr);
    const label = chordLabel(ch.chord);
    if (x1 - x0 > g.measureText(label).width + 4 * dpr) {
      g.fillStyle = on ? "#fff" : cssVar("--text");
      g.fillText(label, x0 + 3 * dpr, h / 2);
    }
  }
}

// ───────── 採譜音・メトロノームの再生 ─────────
const Synth = {
  voices: new Set(), noise: null,
  stopAll() { this.voices.forEach((v) => { try { v.stop(); } catch {} }); this.voices.clear(); },
  track(n) { this.voices.add(n); n.onended = () => this.voices.delete(n); },
  noiseBuf(ctx) {
    if (!this.noise) {
      const b = ctx.createBuffer(1, ctx.sampleRate * 0.3, ctx.sampleRate);
      const d = b.getChannelData(0); for (let i = 0; i < d.length; i++) d[i] = Math.random() * 2 - 1;
      this.noise = b;
    }
    return this.noise;
  },
  tone(ctx, when, pitch, dur, vel) {
    const o = ctx.createOscillator(), g = ctx.createGain();
    o.type = "triangle"; o.frequency.value = 440 * 2 ** ((pitch - 69) / 12);
    const v = 0.12 * (vel / 127);
    g.gain.setValueAtTime(0, when); g.gain.linearRampToValueAtTime(v, when + 0.01);
    g.gain.setTargetAtTime(v * 0.6, when + 0.05, 0.1);
    g.gain.setTargetAtTime(0, when + dur, 0.04);
    o.connect(g).connect(Player.master); o.start(when); o.stop(when + dur + 0.3); this.track(o);
  },
  drum(ctx, when, pitch, vel) {
    const v = 0.4 * (vel / 127);
    if (pitch === 36) {
      const o = ctx.createOscillator(), g = ctx.createGain();
      o.frequency.setValueAtTime(140, when); o.frequency.exponentialRampToValueAtTime(45, when + 0.12);
      g.gain.setValueAtTime(v * 1.5, when); g.gain.exponentialRampToValueAtTime(0.001, when + 0.2);
      o.connect(g).connect(Player.master); o.start(when); o.stop(when + 0.25); this.track(o);
      return;
    }
    const s = ctx.createBufferSource(), f = ctx.createBiquadFilter(), g = ctx.createGain();
    s.buffer = this.noiseBuf(ctx);
    f.type = pitch === 38 ? "bandpass" : "highpass"; f.frequency.value = pitch === 38 ? 1800 : 7000;
    const len = pitch === 38 ? 0.15 : 0.05;
    g.gain.setValueAtTime(v, when); g.gain.exponentialRampToValueAtTime(0.001, when + len);
    s.connect(f).connect(g).connect(Player.master); s.start(when); s.stop(when + len + 0.02); this.track(s);
  },
  click(ctx, when, accent) {
    const o = ctx.createOscillator(), g = ctx.createGain();
    o.frequency.value = accent ? 1600 : 1000;
    g.gain.setValueAtTime(0.25, when); g.gain.exponentialRampToValueAtTime(0.001, when + 0.05);
    o.connect(g).connect(Player.master); o.start(when); o.stop(when + 0.06); this.track(o);
  },
};

const Scheduler = {
  run(t) {
    const ctx = Player.ctx; if (!ctx) return;
    const rate = +$("#rate").value, ahead = 0.25;
    const from = Player.schedCursor, to = t + ahead * rate;
    if (to <= from) return;
    const at = (x) => ctx.currentTime + Math.max(0, (x - t) / rate);
    if ($("#monitor").checked) {
      for (const [part, notes] of monitorParts()) {
        for (const n of notes) {
          if (n.start < from || n.start >= to) continue;
          if (part === "drums") Synth.drum(ctx, at(n.start), n.pitch, n.velocity);
          else Synth.tone(ctx, at(n.start), n.pitch + S.pitch, Math.max(0.05, (n.end - n.start) / rate), n.velocity);
        }
      }
    }
    if (Player.click) {
      const beats = S.job?.analysis?.beats || [];
      beats.forEach((b, i) => { if (b >= from && b < to) Synth.click(ctx, at(b), i % 4 === 0); });
    }
    Player.schedCursor = to;
  },
};
function monitorParts() {
  if (!S.part) return [];
  const names = S.part === "__full" ? Object.keys(S.job.parts) : [S.part];
  return names.filter((n) => S.notes[n]).map((n) => [n, S.notes[n]]);
}

// ───────── パート (楽譜 / ピアノロール) ─────────
async function loadNotes(part) {
  if (!S.notes[part]) S.notes[part] = await api(`/api/jobs/${S.job.id}/notes/${part}`);
  return S.notes[part];
}

async function selectPart(part) {
  S.part = part;
  $$("#partTabs button").forEach((b) => b.classList.toggle("on", b.dataset.part === part));
  const names = part === "__full" ? Object.keys(S.job.parts) : [part];
  await Promise.all(names.map(loadNotes));
  const info = S.job.parts[part];
  const chips = [];
  if (info) {
    chips.push(`<span class="chip">${info.stats.count} ノート</span>`);
    if (part !== "drums" && info.stats.count) chips.push(`<span class="chip">音域 <b>${info.stats.low}–${info.stats.high}</b></span>`);
    if (info.key) chips.push(`<span class="chip">パートのキー ${esc(info.key)}</span>`);
    $("#reSens").value = info.sensitivity ?? 0.5; $("#reSensOut").textContent = (+$("#reSens").value).toFixed(2);
  } else chips.push(`<span class="chip">${names.length} パート</span>`);
  $("#partInfo").innerHTML = chips.join("");
  $("#reBtn").disabled = part === "__full"; $("#reBtn").style.opacity = part === "__full" ? 0.4 : 1;
  applyPlayMode();
  updateDownloads();
  refreshView();
}

// ───────── パートごとの再生 (全パート / このパートだけ / このパートを消す) ─────────
S.playMode = "all";
function applyPlayMode() {
  if (!Player.tracks.length || !S.part) return;
  // ボーカル譜はメインボーカルから作っているので、分離済みならメインボーカルのトラックを対象にする
  const names = (S.part === "__full" ? Object.keys(S.job.parts) : [S.part])
    .map((n) => (n === "vocals" ? leadName() : n));
  Player.tracks.forEach((t) => {
    if (S.playMode === "solo") { t.solo = names.includes(t.name); t.mute = false; }
    else if (S.playMode === "minus") { t.solo = false; t.mute = names.includes(t.name); }
    else { t.solo = false; t.mute = false; }
  });
  Player.applyGains();
}
$$("#playMode button").forEach((b) => (b.onclick = () => {
  S.playMode = b.dataset.mode;
  $$("#playMode button").forEach((x) => x.classList.toggle("on", x === b));
  applyPlayMode();
}));
$("#scorePlay").onclick = () => $("#playBtn").click();

// ───────── 楽譜上の再生位置 (カラオケ風のバー) ─────────
// サーバーの楽譜は「小節頭 (anchor) から一定テンポ」で拍を割り当てているので、同じ計算で時間→小節位置に変換する
const ScoreCursor = {
  geo: [], lastTop: null,
  grid() {
    const a = S.job.analysis, beat = 60 / (a.bpm || 120), bar = 4 * beat;
    const db = a.downbeat ?? a.first_beat ?? 0;
    const n = Math.ceil((db - 0.5 * beat) / bar);
    return { beat, anchor: db - Math.max(0, n) * bar };
  },
  build() {
    this.geo = []; this.lastTop = null;
    const gs = S.osmd?.GraphicSheet; if (!gs) return;
    const svg = $("#osmd svg"); if (!svg) return;
    // バーは #scoreBox 内に重ねるので、楽譜 (svg) の左上の位置を #scoreBox 基準で求める
    const sb = $("#scoreBox"), sr = svg.getBoundingClientRect(), br = sb.getBoundingClientRect();
    const U = 10 * S.osmd.zoom, ox = sr.left - br.left + sb.scrollLeft, oy = sr.top - br.top + sb.scrollTop;
    this.geo = gs.MeasureList.map((staves) => {
      const ms = (staves || []).filter(Boolean);
      if (!ms.length) return null;
      const ps = ms[0].PositionAndShape, begin = ms[0].beginInstructionsWidth || 0;
      const ys = ms.map((m) => m.PositionAndShape.AbsolutePosition.y);
      return {
        x: ox + (ps.AbsolutePosition.x + begin) * U, w: (ps.Size.width - begin) * U,
        bx: ox + ps.AbsolutePosition.x * U, bw: ps.Size.width * U,
        top: oy + (Math.min(...ys) - 1.5) * U, bottom: oy + (Math.max(...ys) + 5.5) * U,
      };
    });
    this.update(Player.time);
  },
  update(t) {
    const line = $("#scoreLine"), box = $("#scoreMeasure");
    if (!this.geo.length || !S.job) { line.hidden = box.hidden = true; return; }
    const { beat, anchor } = this.grid();
    const q = (t - anchor) / beat, m = Math.floor(q / 4), g = this.geo[m];
    if (q < 0 || !g) { line.hidden = box.hidden = true; return; }
    const x = g.x + ((q - m * 4) / 4) * g.w;
    Object.assign(line.style, { left: `${x}px`, top: `${g.top}px`, height: `${g.bottom - g.top}px` });
    Object.assign(box.style, { left: `${g.bx}px`, top: `${g.top}px`, width: `${g.bw}px`, height: `${g.bottom - g.top}px` });
    line.hidden = box.hidden = false;
    // 段が変わったら、バーが画面の上の方に来るようにスクロール
    if (Player.playing && $("#followScore").checked && this.lastTop !== g.top) {
      const r = line.getBoundingClientRect();
      if (r.top < 140 || r.bottom > innerHeight - 150) scrollBy({ top: r.top - innerHeight * 0.3, behavior: "smooth" });
      const sb = $("#scoreBox"), lx = x - sb.scrollLeft;
      if (lx < 20 || lx > sb.clientWidth - 40) sb.scrollTo({ left: x - sb.clientWidth / 3, behavior: "smooth" });
    }
    this.lastTop = g.top;
  },
  // 楽譜をクリックした位置の小節へ移動
  seekAt(e) {
    const sb = $("#scoreBox"), r = sb.getBoundingClientRect();
    const px = e.clientX - r.left + sb.scrollLeft, py = e.clientY - r.top + sb.scrollTop;
    const m = this.geo.findIndex((g) => g && px >= g.bx && px <= g.bx + g.bw && py >= g.top && py <= g.bottom);
    if (m < 0) return;
    const g = this.geo[m], frac = Math.min(1, Math.max(0, (px - g.x) / g.w));
    const { beat, anchor } = this.grid();
    Player.seek(anchor + (m * 4 + frac * 4) * beat);
  },
};
$("#osmd").addEventListener("click", (e) => ScoreCursor.seekAt(e));
addEventListener("resize", () => setTimeout(() => ScoreCursor.build(), 300));

function scoreQuery(extra = "") {
  const parts = S.part === "__full" ? "" : S.part;
  return `parts=${parts}&transpose=${S.transpose}&division=${$("#division").value}&chords=${$("#chordSyms").checked}&names=${$("#noteNames").value}${extra}`;
}
function updateDownloads() {
  if (!S.job || !S.part) return;
  $("#xmlBtn").href = `/api/jobs/${S.job.id}/score?${scoreQuery("&download=true")}`;
  $("#midBtn").href = `/api/jobs/${S.job.id}/midi?parts=${S.part === "__full" ? "" : S.part}&transpose=${S.transpose}`;
}

function refreshView() {
  $("#scoreBox").hidden = S.view !== "score";
  $("#rollBox").hidden = S.view !== "roll";
  if (S.view === "score") renderScore(); else Roll.draw(Player.time);
}

async function renderScore() {
  const req = ++S.scoreReq;
  const msg = $("#scoreMsg");
  msg.textContent = "楽譜を生成中…（長い曲は数十秒かかります）";
  $("#osmd").style.opacity = 0.3;
  try {
    const xml = await api(`/api/jobs/${S.job.id}/score?${scoreQuery()}`);
    if (req !== S.scoreReq) return;
    if (!window.opensheetmusicdisplay) throw new Error("楽譜表示ライブラリを読み込めませんでした (ネット接続を確認)。MusicXML のダウンロードは利用できます");
    if (!S.osmd) {
      S.osmd = new opensheetmusicdisplay.OpenSheetMusicDisplay("osmd", {
        autoResize: true, backend: "svg", drawTitle: !isMobile(), drawSubtitle: false, drawComposer: false, drawCredits: false,
        drawingParameters: "default", drawPartNames: true,
        // 音の名前ごとに色分け (同じ音名は同じ色)
        coloringEnabled: $("#colorNotes").checked,
        coloringMode: $("#colorNotes").checked ? 1 : 0,
        colorStemsLikeNoteheads: true,
      });
    }
    await S.osmd.load(xml);
    if (req !== S.scoreReq) return;
    S.osmd.zoom = +$("#zoom").value;
    S.osmd.render();
    ScoreCursor.build();
    msg.textContent = "";
  } catch (e) {
    if (req === S.scoreReq) msg.textContent = "楽譜を表示できませんでした: " + e.message;
  } finally {
    if (req === S.scoreReq) $("#osmd").style.opacity = 1;
  }
}

$$("#viewSeg button").forEach((b) => (b.onclick = () => {
  S.view = b.dataset.view;
  $$("#viewSeg button").forEach((x) => x.classList.toggle("on", x === b));
  refreshView();
}));
function setTranspose(v, fromPreset = false) {
  S.transpose = Math.max(-12, Math.min(12, v));
  $("#tOut").textContent = (S.transpose > 0 ? "+" : "") + S.transpose;
  if (!fromPreset) {
    const opt = [...$("#transposePreset").options].find((o) => o.value === String(S.transpose));
    $("#transposePreset").value = opt ? opt.value : "custom";
  }
  updateDownloads();
  if (S.part) refreshView();
}
$("#transposePreset").onchange = (e) => { if (e.target.value !== "custom") setTranspose(+e.target.value, true); };
$("#tUp").onclick = () => setTranspose(S.transpose + 1);
$("#tDown").onclick = () => setTranspose(S.transpose - 1);
$("#division").onchange = () => { updateDownloads(); refreshView(); };
$("#chordSyms").onchange = () => { updateDownloads(); refreshView(); };
$("#noteNames").onchange = () => { store.set("noteNames", $("#noteNames").value); updateDownloads(); refreshView(); };
$("#colorNotes").onchange = () => { store.set("colorNotes", $("#colorNotes").checked); S.osmd = null; $("#osmd").innerHTML = ""; refreshView(); };
$("#zoom").oninput = () => {
  if (S.view === "score" && S.osmd?.IsReadyToRender?.() !== false && S.osmd) {
    S.osmd.zoom = +$("#zoom").value;
    try { S.osmd.render(); ScoreCursor.build(); } catch {}
  } else Roll.draw(Player.time);
};
$("#reSens").oninput = (e) => ($("#reSensOut").textContent = (+e.target.value).toFixed(2));
$("#reBtn").onclick = async () => {
  const part = S.part; if (!part || part === "__full") return;
  const btn = $("#reBtn"); btn.disabled = true; btn.textContent = "再採譜中…";
  try {
    const info = await api(`/api/jobs/${S.job.id}/retranscribe/${part}?sensitivity=${$("#reSens").value}`, { method: "POST" });
    S.job.parts[part] = info; delete S.notes[part];
    toast(`${STEM_INFO[part]?.label ?? part}: ${info.stats.count} ノートで再採譜しました`);
    await selectPart(part);
  } catch (e) { toast("エラー: " + e.message); }
  finally { btn.disabled = false; btn.textContent = "このパートを再採譜"; }
};
$("#printBtn").onclick = () => {
  if (S.view !== "score") $$("#viewSeg button")[0].click();
  setTimeout(() => print(), 300);
};
$("#monitor").onchange = () => { Player.schedCursor = Player.time; Synth.stopAll(); };

// ピアノロールの音名 (楽譜の音名設定に合わせる)
function rollLabel(p) {
  const mode = $("#noteNames").value || "letter";
  const n = NOTE_NAMES[p % 12];
  return mode === "letter_oct" ? midiName(p) : n.replace("#", "♯");
}

// ピアノロール
const Roll = {
  draw(t) {
    const c = $("#roll"); if ($("#rollBox").hidden || !S.part) return;
    const dpr = devicePixelRatio || 1, W = c.clientWidth, H = c.clientHeight;
    if (!W) return;
    if (c.width !== Math.round(W * dpr)) { c.width = Math.round(W * dpr); c.height = Math.round(H * dpr); }
    const g = c.getContext("2d"); g.setTransform(dpr, 0, 0, dpr, 0, 0);
    g.fillStyle = cssVar("--panel-2"); g.fillRect(0, 0, W, H);
    const parts = monitorParts();
    const all = parts.flatMap(([p, ns]) => ns.map((n) => ({ ...n, part: p, pitch: p === "drums" ? n.pitch : n.pitch + S.pitch })));
    const drumsOnly = parts.length === 1 && parts[0][0] === "drums";
    const keyW = 44, top = 18;
    // 表示範囲 (zoom で秒数を変える)
    const win = 12 / (+$("#zoom").value || 1);
    this.win = win;
    const t0 = Math.max(0, t - win * 0.25), t1 = t0 + win;
    this.t0 = t0; this.keyW = keyW;
    const X = (s) => keyW + ((s - t0) / win) * (W - keyW);
    let rows;
    if (drumsOnly) rows = [49, 42, 45, 38, 36];
    else {
      const ps = all.filter((n) => n.part !== "drums").map((n) => n.pitch);
      let lo = ps.length ? Math.min(...ps) - 2 : 48, hi = ps.length ? Math.max(...ps) + 2 : 72;
      if (hi - lo < 24) { const m = (hi + lo) >> 1; lo = m - 12; hi = m + 12; }
      rows = []; for (let p = hi; p >= lo; p--) rows.push(p);
    }
    const rh = (H - top) / rows.length;
    const rowOf = new Map(rows.map((p, i) => [p, i]));
    // 行
    rows.forEach((p, i) => {
      const black = !drumsOnly && [1, 3, 6, 8, 10].includes(p % 12);
      g.fillStyle = black ? "rgba(0,0,0,.06)" : "rgba(0,0,0,0)";
      g.fillRect(keyW, top + i * rh, W - keyW, rh);
      if (drumsOnly || p % 12 === 0 || rh > 11) {
        g.fillStyle = cssVar("--muted"); g.font = "10px sans-serif"; g.textBaseline = "middle";
        g.fillText(drumsOnly ? DRUM_NAMES[p] : midiName(p), 4, top + i * rh + rh / 2);
        if (!drumsOnly && p % 12 === 0) { g.fillStyle = "rgba(127,127,127,.25)"; g.fillRect(keyW, top + (i + 1) * rh - 1, W - keyW, 1); }
      }
    });
    // 拍・小節線
    (S.job.analysis.beats || []).forEach((b, i) => {
      if (b < t0 || b > t1) return;
      g.fillStyle = i % 4 === 0 ? "rgba(127,127,127,.45)" : "rgba(127,127,127,.15)";
      g.fillRect(X(b), top, 1, H - top);
    });
    // コード
    g.font = "11px sans-serif"; g.textBaseline = "middle";
    for (const ch of chordsOf()) {
      if (ch.end < t0 || ch.start > t1 || ch.chord === "N") continue;
      g.fillStyle = cssVar("--accent"); g.fillText(chordLabel(ch.chord), Math.max(keyW, X(ch.start)) + 2, 9);
    }
    // ノート
    for (const n of all) {
      if (n.end < t0 || n.start > t1) continue;
      const r = drumsOnly ? rowOf.get(n.pitch) : (n.part === "drums" ? undefined : rowOf.get(n.pitch));
      if (r === undefined) continue;
      const x = X(n.start), w = Math.max(3, X(n.end) - x);
      const on = t >= n.start && t < n.end;
      g.globalAlpha = 0.45 + 0.55 * (n.velocity / 127);
      g.fillStyle = STEM_INFO[n.part]?.color || "#888";
      g.fillRect(x, top + r * rh + 1, w, Math.max(2, rh - 2));
      if (!drumsOnly && n.part !== "drums" && rh >= 9 && w >= 16) {
        const lbl = rollLabel(n.pitch);
        g.font = `${Math.min(12, rh - 2)}px sans-serif`;
        if (g.measureText(lbl).width + 4 <= w) {
          g.globalAlpha = 1; g.fillStyle = "#fff"; g.textBaseline = "middle";
          g.fillText(lbl, x + 2, top + r * rh + rh / 2);
        }
      }
      if (on) { g.globalAlpha = 1; g.strokeStyle = cssVar("--text"); g.strokeRect(x, top + r * rh + 1, w, Math.max(2, rh - 2)); }
      g.globalAlpha = 1;
    }
    // 再生位置
    g.fillStyle = cssVar("--text"); g.fillRect(X(t), 0, 1.5, H);
    g.fillStyle = cssVar("--panel"); g.fillRect(0, 0, keyW, top);
  },
};
$("#roll").addEventListener("pointerdown", (e) => {
  const r = e.currentTarget.getBoundingClientRect(), x = e.clientX - r.left;
  if (x < Roll.keyW) return;
  Player.seek(Roll.t0 + ((x - Roll.keyW) / (r.width - Roll.keyW)) * Roll.win);
});

// ───────── キーボード ─────────
addEventListener("keydown", (e) => {
  if (e.target.closest("input[type=url],input[type=number],select,textarea") || !Player.tracks.length) return;
  const k = e.key;
  if (k === " ") { e.preventDefault(); Player.playing ? Player.pause() : Player.play(); }
  else if (k === "ArrowLeft") Player.seek(Player.time - 5);
  else if (k === "ArrowRight") Player.seek(Player.time + 5);
  else if (k === "l" || k === "L") Player.setLoop(!Player.loop);
  else if (k === "c" || k === "C") $("#clickBtn").click();
  else if (k === "[") Player.setRate(Math.max(0.5, +$("#rate").value - 0.05));
  else if (k === "]") Player.setRate(Math.min(1.5, +$("#rate").value + 0.05));
  else if (/^[1-6]$/.test(k)) {
    const tr = Player.tracks[+k - 1]; if (!tr) return;
    const only = tr.solo && Player.tracks.filter((t) => t.solo).length === 1;
    Player.tracks.forEach((t) => (t.solo = false)); tr.solo = !only; Player.applyGains();
  } else if (k === "0") { Player.tracks.forEach((t) => (t.solo = false)); Player.applyGains(); }
});

// ───────── 起動 ─────────
// ───────── スマホ接続 (PC の画面のみ) ─────────
async function showConnect(data) {
  $("#qrBox").innerHTML = data.qr || "<p class='hint'>ネットワークが見つかりません</p>";
  $("#connectUrls").innerHTML = data.urls.map((u) => `<a href="${esc(u)}" target="_blank" rel="noopener">${esc(u)}</a>`).join("");
  $("#connectKey").textContent = data.key;
  $("#lanWarn").hidden = data.lan;
}
$("#connectBtn").onclick = async () => {
  try {
    const [conn] = await Promise.all([api("/api/connect"), loadMembers()]);
    showConnect(conn);
    $("#newInvite").hidden = true;
    $("#connectDlg").showModal();
  } catch (e) { toast(e.message); }
};

// ───────── メンバーの招待 (管理者のみ) ─────────
async function loadMembers() {
  const list = await api("/api/members");
  $("#memberList").innerHTML = list.length ? list.map((m) => `<li data-id="${esc(m.id)}">
      <b>${esc(m.name)}</b><small>${m.songs} 曲</small>
      <button class="ghost small" data-act="show">リンク</button>
      <button class="ghost small danger" data-act="del">取り消し</button></li>`).join("")
    : `<li class="hint">まだ誰も招待していません</li>`;
  $("#memberList").querySelectorAll("li[data-id]").forEach((li) => {
    const m = list.find((x) => x.id === li.dataset.id);
    li.querySelector('[data-act="show"]').onclick = () => showInvite(m);
    li.querySelector('[data-act="del"]').onclick = async () => {
      if (!confirm(`${m.name} さんの招待を取り消しますか？（その人は StemScore を開けなくなります。処理済みの曲はあなたの履歴に残ります）`)) return;
      await api(`/api/members/${m.id}`, { method: "DELETE" });
      $("#newInvite").hidden = true;
      loadMembers();
    };
  });
}
function showInvite(m) {
  const box = $("#newInvite");
  box.innerHTML = m.url ? `<div><b>${esc(m.name)}</b> さんの招待リンク</div>${m.qr || ""}
    <input readonly value="${esc(m.url)}"><div class="row-btns" style="justify-content:center;margin-top:8px">
    <button class="ghost small" type="button" id="copyInvite">リンクをコピー</button>
    ${navigator.share ? '<button class="ghost small" type="button" id="shareInvite">送る…</button>' : ""}</div>
    <p class="hint small">このリンクを開くだけで使えるようになります。他の人には転送しないよう伝えてください。</p>`
    : `<p class="hint">ネットワークが見つからないため、リンクを作れませんでした</p>`;
  box.hidden = false;
  box.querySelector("#copyInvite")?.addEventListener("click", async () => {
    try { await navigator.clipboard.writeText(m.url); toast("コピーしました"); }
    catch { box.querySelector("input").select(); toast("選択したのでコピーしてください"); }
  });
  box.querySelector("#shareInvite")?.addEventListener("click", () =>
    navigator.share({ title: "StemScore の招待", text: `${m.name} さん用の StemScore のリンクです`, url: m.url }).catch(() => {}));
}
$("#addMemberBtn").onclick = async () => {
  const name = $("#memberName").value.trim();
  if (!name) return toast("名前を入れてください");
  const fd = new FormData(); fd.append("name", name);
  try {
    const m = await api("/api/members", { method: "POST", body: fd });
    $("#memberName").value = "";
    showInvite(m);
    loadMembers();
  } catch (e) { toast(e.message); }
};
$("#resetKeyBtn").onclick = async () => {
  if (!confirm("キーを再発行すると、登録済みのスマホも再度 QR の読み取りが必要になります。")) return;
  showConnect(await api("/api/connect/reset", { method: "POST" }));
};

(async function boot() {
  if (isMobile()) { $("#zoom").value = 0.6; }
  const nn = store.get("noteNames", "letter");
  $("#noteNames").value = ["letter", "letter_oct", ""].includes(nn) ? nn : "letter";
  $("#countIn").checked = store.get("countIn", true);
  $("#colorNotes").checked = store.get("colorNotes", true);
  setTab("add");
  api("/api/me").then((me) => {
    S.me = me;
    $("#connectBtn").hidden = !me.admin;
    $("#modelCard").hidden = !me.admin;   // モデルの追加は管理者だけ
    if (!me.admin) { $("#meChip").hidden = false; $("#meChip").textContent = `${me.name} さん`; }
  }).catch(() => {});
  if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js").catch(() => {});
  refreshModels();
  api("/api/config").then((cfg) => {
    S.cfg = cfg;
    $("#splitVocalsRow").hidden = !cfg.karaoke?.installed;
    $("#inboxTab").hidden = !cfg.inbox;
    if (cfg.upload_limit_mb) {
      $("#uploadLimitHint").hidden = false;
      $("#uploadLimitHint").textContent = `1 回に送れるのは ${cfg.upload_limit_mb}MB まで。大きい動画は Drive の「曲」フォルダ経由で。`;
    }
  }).catch(() => {});
  await refreshList();
  setInterval(refreshList, 4000);
  const last = store.get("lastJob", null);
  if (last && S.jobs.some((j) => j.id === last)) {
    openJob.silent = true;
    await openJob(last).catch(() => {});
    openJob.silent = false;
  }
  if (isMobile() && S.jobs.length) setTab(S.job ? "song" : "list");
})();
