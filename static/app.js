const $ = (s) => document.querySelector(s);
const api = (url, opts) => fetch(url, opts).then(r => r.json());

let jobId = null;
let burnId = null;
let playlist = [];        // ordered track ids for burning
let searchGroups = [];

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, c =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

const fmtTime = (sec) => sec == null ? "" :
  `${Math.floor(sec / 60)}:${String(sec % 60).padStart(2, "0")}`;

const fmtTotal = (sec) => `${Math.floor(sec / 60)} min ${String(sec % 60).padStart(2, "0")} sec`;

function setStep(n) {
  document.querySelectorAll(".step").forEach(s => {
    const i = +s.dataset.s;
    s.classList.toggle("active", i === n);
    s.classList.toggle("done", i < n);
  });
}

// ============ 1 · add ============
function parseTitles() {
  return $("#titles").value.split("\n").map(s => s.trim()).filter(Boolean);
}

async function doSearch(titles) {
  if (!titles.length) return;
  setStep(2);
  $("#btn-add").disabled = true;
  $("#s-search").classList.remove("hidden");
  $("#search-results").innerHTML = '<p class="muted">Searching…</p>';
  const res = await api("/api/search", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ titles }),
  });
  renderResults(res.results);
  $("#btn-add").disabled = false;
}

$("#btn-add").addEventListener("click", () => doSearch(parseTitles()));
$("#btn-load").addEventListener("click", () => $("#file").click());
$("#file").addEventListener("change", (e) => {
  const f = e.target.files[0];
  if (!f) return;
  const reader = new FileReader();
  reader.onload = () => { $("#titles").value = reader.result; doSearch(parseTitles()); };
  reader.readAsText(f);
});

const dz = $("#dropzone");
["dragover", "dragenter"].forEach(ev => dz.addEventListener(ev, (e) => {
  e.preventDefault(); dz.classList.add("over");
}));
["dragleave", "drop"].forEach(ev => dz.addEventListener(ev, e => e.preventDefault()));
dz.addEventListener("drop", (e) => {
  e.preventDefault(); dz.classList.remove("over");
  const f = e.dataTransfer.files[0];
  if (f) {
    const reader = new FileReader();
    reader.onload = () => { $("#titles").value = reader.result; doSearch(parseTitles()); };
    reader.readAsText(f);
  }
});

// ---------- playlist import ----------
async function importPlaylist(url) {
  const note = $("#import-note");
  note.classList.add("hidden");
  const r = await api("/api/import", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ url }),
  });
  if (r.needs_creds) {
    note.textContent = r.error;
    note.classList.remove("hidden");
    $("#spotify-creds").classList.remove("hidden");
    return;
  }
  if (r.error) {
    note.textContent = r.error;
    note.classList.remove("hidden");
    return;
  }
  $("#import-url").value = "";
  $("#titles").value = r.songs.map(s => s.artist ? `${s.artist} - ${s.title}` : s.title).join("\n");

  const mt = await api("/api/import-match", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ songs: r.songs }),
  });
  if (mt.error) {
    note.textContent = mt.error;
    note.classList.remove("hidden");
    return;
  }

  setStep(2);
  $("#s-search").classList.remove("hidden");
  const summary = $("#match-summary");
  summary.classList.remove("hidden");
  summary.innerHTML = "";
  if (mt.matched.length) {
    const line = document.createElement("div");
    line.className = "matchline";
    line.innerHTML = `<strong>${mt.matched.length}</strong> of ${r.songs.length} songs auto-matched to the originals.`;
    const btn = document.createElement("button");
    btn.className = "primary";
    btn.textContent = `Download all (${mt.matched.length})`;
    btn.addEventListener("click", () => startDownload(mt.matched));
    summary.appendChild(line);
    summary.appendChild(btn);
  }

  if (mt.unmatched.length) {
    const note = $("#search-note");
    note.textContent = `${mt.unmatched.length} song${mt.unmatched.length > 1 ? "s" : ""} didn't auto-match — pick below.`;
    note.classList.remove("hidden");
    await doSearch(mt.unmatched.map(u => u.title));
    const btn = $("#btn-download");
    btn.textContent = `Download the ${mt.unmatched.length} unmatched`;
    btn.classList.remove("hidden");
  } else if (!mt.matched.length) {
    summary.innerHTML = '<div class="muted">Nothing could be auto-matched.</div>';
  }
}

$("#btn-import").addEventListener("click", () => importPlaylist($("#import-url").value.trim()));

$("#btn-save-creds").addEventListener("click", async () => {
  await api("/api/config", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      spotify_client_id: $("#sid").value.trim(),
      spotify_client_secret: $("#ssec").value.trim(),
    }),
  });
  $("#spotify-creds").classList.add("hidden");
  importPlaylist($("#import-url").value.trim());
});

// ============ 2 · matches ============
function renderResults(results) {
  const wrap = $("#search-results");
  wrap.innerHTML = "";
  searchGroups = [];
  results.forEach((g, gi) => {
    const div = document.createElement("div");
    div.className = "group";
    if (g.error) {
      div.innerHTML = `<div class="err">${gi + 1}. No results for “${esc(g.query)}”</div>`;
      wrap.appendChild(div);
      return;
    }
    const dupe = g.dupe_of !== undefined
      ? ` <span class="chip warn" title="Same song entered twice">dupe of #${g.dupe_of + 1}</span>`
      : "";
    div.innerHTML += `<div class="q">${gi + 1}. ${esc(g.query)}${dupe}</div>`;
    g.candidates.forEach((c, ci) => {
      const row = document.createElement("label");
      row.className = "cand";
      row.innerHTML = `
        <input type="radio" name="g${gi}" class="radio" ${ci === 0 ? "checked" : ""}>
        <img src="${esc(c.thumb)}" alt="" onerror="this.style.display='none'">
        <span class="meta">
          <span class="t">${esc(c.title)}</span>
          <span class="a">${esc(c.artist)}</span>
        </span>
        <span class="d">${fmtTime(c.duration)}</span>
        <button type="button" class="mini preview-btn" data-id="${esc(c.id)}" data-title="${esc(c.title)}">▶</button>`;
      row.addEventListener("click", (e) => {
        if (e.target.classList.contains("preview-btn")) return;
        [...row.parentNode.querySelectorAll(".cand")].forEach(x => x.classList.remove("sel"));
        row.classList.add("sel");
        row.querySelector("input").checked = true;
      });
      div.appendChild(row);
    });
    wrap.appendChild(div);
    searchGroups.push({ query: g.query, candidates: g.candidates });
  });
  attachPreview();
  const n = searchGroups.length;
  if (n) {
    $("#search-count").textContent = `${n} songs`;
    $("#search-count").className = "chip good";
    $("#search-count").classList.remove("hidden");
    $("#btn-download").textContent = `Download selected (${n})`;
    $("#btn-download").classList.remove("hidden");
  }
}

// ---------- audio preview ----------
const previewAudio = $("#preview");
let previewingId = null;

function attachPreview() {
  document.querySelectorAll(".preview-btn").forEach(btn => {
    btn.addEventListener("click", async (e) => {
      e.preventDefault();
      e.stopPropagation();
      const id = btn.dataset.id;
      if (previewingId === id) {
        previewAudio.pause();
        previewingId = null;
        btn.textContent = "▶";
        return;
      }
      document.querySelectorAll(".preview-btn").forEach(b => (b.textContent = "▶"));
      btn.textContent = "…";
      const r = await api(`/api/preview/${id}`);
      if (!r.ok) {
        btn.textContent = "▶";
        previewingId = null;
        const note = $("#search-note");
        note.textContent = "Preview failed: " + (r.error || "no stream available");
        note.classList.remove("hidden");
        setTimeout(() => note.classList.add("hidden"), 4000);
        return;
      }
      previewingId = id;
      previewAudio.src = r.url;
      previewAudio.classList.remove("hidden");
      previewAudio.style.width = "100%";
      previewAudio.play().catch((err) => {
        const note = $("#search-note");
        note.textContent = "Playback failed: " + (err && err.name ? err.name : err);
        note.classList.remove("hidden");
        setTimeout(() => note.classList.add("hidden"), 4000);
        document.querySelectorAll(".preview-btn").forEach(b => (b.textContent = "▶"));
        previewingId = null;
      });
      btn.textContent = "■";
      previewAudio.onended = () => {
        if (previewingId) {
          document.querySelectorAll(".preview-btn").forEach(b => (b.textContent = "▶"));
          previewingId = null;
        }
      };
    });
  });
}

// ============ 3 · download ============
$("#btn-download").addEventListener("click", async () => {
  const groups = [...document.querySelectorAll(".group")].filter(g => !g.querySelector(".err"));
  const picked = groups.map((g, gi) => {
    const idx = [...g.querySelectorAll("input")].findIndex(i => i.checked);
    const c = searchGroups[gi].candidates[idx] || searchGroups[gi].candidates[0];
    return { id: c.id, title: c.title, artist: c.artist, album: c.album || "", duration: c.duration };
  });
  await startDownload(picked);
});

async function startDownload(items) {
  setStep(3);
  $("#s-dl").classList.remove("hidden");
  $("#dl-items").innerHTML = items.map(it =>
    `<div class="dl-item" data-id="${it.id}"><span class="t">${esc(it.title)}</span><span class="st">queued</span></div>`).join("");
  $("#dl-count").textContent = `0 / ${items.length}`;
  const res = await api("/api/download", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ items }),
  });
  jobId = res.job;
  pollDownload();
}

async function pollDownload() {
  const j = await api(`/api/status/${jobId}`);
  const pct = j.total ? Math.round((j.done / j.total) * 100) : 0;
  $("#dl-bar").style.width = pct + "%";
  $("#dl-count").textContent = `${j.done} / ${j.total}`;
  $("#dl-status").textContent = j.current ? `Working on “${j.current}”…` : (j.done ? "Converting…" : "Waiting…");
  j.items.forEach(it => {
    let row = document.querySelector(`.dl-item[data-id="${it.id}"]`);
    if (!row) {
      row = document.createElement("div");
      row.className = "dl-item";
      row.dataset.id = it.id;
      row.innerHTML = `<span class="t">${esc(it.title)}</span><span class="st">queued</span>`;
      $("#dl-items").appendChild(row);
    }
    row.querySelector(".st").textContent = it.status;
    row.querySelector(".st").className = "st " + it.status;
  });
  if (j.state === "working") {
    setTimeout(pollDownload, 700);
  } else if (j.state === "done") {
    $("#dl-status").textContent = "All done.";
    setStep(4);
    showBurn();
  } else {
    $("#dl-status").textContent = "Download failed: " + (j.error || "");
  }
}

// ============ 4 · burn ============
async function showBurn() {
  $("#s-burn").classList.remove("hidden");
  loadDrives();
  const d = await api("/api/songs");
  playlist = d.items.filter(it => it.status === "done").map(it => it.id);
  renderPlaylist(d);
}

function renderPlaylist(data) {
  const wrap = $("#playlist");
  const ready = data.items.filter(it => it.status === "done");
  const failed = data.items.filter(it => it.status === "error");
  $("#pl-count").textContent = `${ready.length} of ${data.items.length} ready`;

  if (!ready.length) {
    wrap.innerHTML = '<div class="empty">No downloaded tracks yet.</div>';
    $("#time-info").textContent = "";
  } else {
    wrap.innerHTML = "";
    playlist.forEach((id, i) => {
      const it = data.items.find(x => x.id === id);
      if (!it) return;
      const row = document.createElement("div");
      row.className = "track";
      row.dataset.id = id;
      row.innerHTML = `
        <span class="num">${i + 1}</span>
        <span class="meta">
          <span class="t">${esc(it.title)}${it.dupe ? ' <span class="chip warn">dupe</span>' : ""}</span>
          <span class="a">${esc(it.artist)}</span>
        </span>
        <span class="d">${fmtTime(it.duration)}</span>
        <span class="actions">
          <button class="mini" data-act="up" ${i === 0 ? "disabled" : ""}>↑</button>
          <button class="mini" data-act="down" ${i === playlist.length - 1 ? "disabled" : ""}>↓</button>
          <button class="mini" data-act="rm" title="Remove">✕</button>
        </span>`;
      wrap.appendChild(row);
    });
  }

  renderAttention(failed);

  const totalSecs = data.total_secs;
  const dupes = data.items.filter(it => it.dupe).length;
  if (ready.length) {
    let time = `Total play time: ${fmtTotal(totalSecs)}${totalSecs > 74 * 60 ? " — over 74 min, check disc capacity" : ""}`;
    if (dupes) time += ` · ⚠ ${dupes} possible duplicate${dupes > 1 ? "s" : ""}`;
    $("#time-info").textContent = time;
    $("#time-info").className = "chip " + ((totalSecs > 74 * 60 || dupes) ? "warn" : "good");
  }
  $("#burn-hint").textContent = playlist.length ? "Order the tracks, then burn." : "";
}

function renderAttention(failed) {
  const block = $("#attention-block");
  const wrap = $("#attention");
  if (!failed.length) {
    block.classList.add("hidden");
    return;
  }
  block.classList.remove("hidden");
  $("#att-count").textContent = `${failed.length} failed`;
  wrap.innerHTML = "";
  failed.forEach(it => {
    const row = document.createElement("div");
    row.className = "track";
    row.dataset.id = it.id;
    row.innerHTML = `
      <span class="num bad">✕</span>
      <span class="meta">
        <span class="t">${esc(it.title)}</span>
        <span class="a">${esc(it.artist)}</span>
        <span class="errline">${esc(it.error || "download failed")}</span>
      </span>
      <span class="actions">
        <button class="mini" data-act="retry">Retry</button>
        <button class="mini" data-act="swap">Swap</button>
        <button class="mini" data-act="rm">✕</button>
      </span>`;
    wrap.appendChild(row);
  });
}

$("#attention").addEventListener("click", async (e) => {
  const btn = e.target.closest("button[data-act]");
  if (!btn) return;
  const id = btn.closest(".track").dataset.id;
  const d = await api("/api/songs");
  const it = d.items.find(x => x.id === id);
  if (!it) return;
  if (btn.dataset.act === "retry") {
    await startDownload([{ id: it.id, title: it.title, artist: it.artist, album: it.album || "", duration: it.duration }]);
  } else if (btn.dataset.act === "rm") {
    await api("/api/remove", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ id }) });
    await refreshPlaylist();
  } else if (btn.dataset.act === "swap") {
    swapCandidates(it, btn.closest(".track"));
  }
});

async function swapCandidates(item, row) {
  let holder = row.parentNode.querySelector(".candgroup");
  if (holder) { holder.remove(); return; }
  holder = document.createElement("div");
  holder.className = "candgroup";
  holder.innerHTML = '<div class="muted" style="padding:6px">Searching alternatives…</div>';
  row.after(holder);
  const res = await api("/api/search", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ titles: [item.title] }),
  });
  const g = res.results[0];
  if (!g || g.error || !g.candidates.length) {
    holder.innerHTML = '<div class="muted" style="padding:6px">No alternatives found.</div>';
    return;
  }
  holder.innerHTML = "";
  g.candidates.forEach(c => {
    const lab = document.createElement("label");
    lab.className = "cand";
    lab.innerHTML = `
      <img src="${esc(c.thumb)}" alt="" onerror="this.style.display='none'">
      <span class="meta">
        <span class="t">${esc(c.title)}</span>
        <span class="a">${esc(c.artist)}</span>
      </span>
      <span class="d">${fmtTime(c.duration)}</span>`;
    lab.addEventListener("click", async () => {
      lab.style.opacity = "0.5";
      await api("/api/remove", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ id: item.id }) });
      await startDownload([{ id: c.id, title: c.title, artist: c.artist, album: c.album || "", duration: c.duration }]);
    });
    holder.appendChild(lab);
  });
}

$("#playlist").addEventListener("click", (e) => {
  const btn = e.target.closest("button[data-act]");
  if (!btn) return;
  const id = btn.closest(".track").dataset.id;
  const idx = playlist.indexOf(id);
  if (btn.dataset.act === "up" && idx > 0) {
    [playlist[idx - 1], playlist[idx]] = [playlist[idx], playlist[idx - 1]];
  } else if (btn.dataset.act === "down" && idx < playlist.length - 1) {
    [playlist[idx + 1], playlist[idx]] = [playlist[idx], playlist[idx + 1]];
  } else if (btn.dataset.act === "rm") {
    playlist.splice(idx, 1);
  }
  refreshPlaylist();
});

async function refreshPlaylist() {
  const d = await api("/api/songs");
  renderPlaylist(d);
}

async function loadDrives() {
  const d = await api("/api/drives");
  const sel = $("#drive");
  sel.innerHTML = "";
  const warn = $("#disc-info");
  warn.classList.add("hidden");
  if (!d.ok) {
    sel.innerHTML = `<option value="">No drive found</option>`;
    warn.textContent = d.error || "No drive found on this PC.";
    warn.classList.remove("hidden");
    return;
  }
  d.drives.forEach(x => {
    const o = document.createElement("option");
    o.value = x.letter;
    o.textContent = `${x.letter} — ${x.description}`;
    sel.appendChild(o);
  });
  if (d.mediaLoaded === false) {
    warn.textContent = "No disc in the drive — insert a blank CD-R.";
    warn.classList.remove("hidden");
  } else if (d.blank === false) {
    warn.textContent = "Disc in drive is not blank — insert a blank CD-R.";
    warn.classList.remove("hidden");
  }
}

$("#btn-burn").addEventListener("click", async () => {
  if (!playlist.length) return;
  const res = await api("/api/burn", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      drive: $("#drive").value,
      mode: $("#mode").value,
      speed_kb: parseInt($("#speed").value, 10),
      ids: playlist,
    }),
  });
  if (res.error) { alert(res.error); return; }
  burnId = res.burn;
  $("#btn-burn").disabled = true;
  $("#burn-box").classList.remove("hidden");
  $("#burn-log").innerHTML = "";
  pollBurn();
});

async function pollBurn() {
  const b = await api(`/api/burnstatus/${burnId}`);
  const log = $("#burn-log");
  if (b.log !== log.dataset.last) {
    log.innerHTML = renderLog(b.log);
    log.dataset.last = b.log;
    log.scrollTop = log.scrollHeight;
  }
  if (b.state === "working") {
    setTimeout(pollBurn, 900);
  } else {
    $("#btn-burn").disabled = false;
    if (b.state === "done") {
      appendLog("✔ Disc burned successfully — enjoy the ride.");
    } else {
      appendLog("✖ " + (b.message || "Burning failed."));
    }
    setStep(4);
  }
}

function renderLog(text) {
  return esc(text)
    .split("\n")
    .map(l => {
      let cls = "";
      if (/^TRACK /.test(l)) cls = "accent";
      else if (/^BURN COMPLETE|^OK$/.test(l)) cls = "ok";
      else if (/^WARN/.test(l)) cls = "warnl";
      else if (/^Staging /.test(l)) cls = "";
      return cls ? `<span class="${cls}">${l}</span>` : l;
    })
    .join("\n");
}

function appendLog(text) {
  const log = $("#burn-log");
  log.innerHTML += "\n" + renderLog(text);
  log.scrollTop = log.scrollHeight;
}