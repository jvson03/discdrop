const $ = (s) => document.querySelector(s);
const api = (url, opts) => fetch(url, opts).then(r => r.json());

let jobId = null;
let burnId = null;
let selections = [];
let searchGroups = [];

// ---------- step 1: add songs ----------
function parseTitles() {
  return $("#titles").value.split("\n").map(s => s.trim()).filter(Boolean);
}

$("#btn-add").addEventListener("click", async () => {
  const titles = parseTitles();
  if (!titles.length) return;
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
});

$("#btn-load").addEventListener("click", () => $("#file").click());
$("#file").addEventListener("change", (e) => {
  const f = e.target.files[0];
  if (!f) return;
  const reader = new FileReader();
  reader.onload = () => {
    $("#titles").value = reader.result;
    $("#btn-add").click();
  };
  reader.readAsText(f);
});

const dz = $("#dropzone");
["dragover", "dragenter"].forEach(ev => dz.addEventListener(ev, (e) => {
  e.preventDefault();
  dz.classList.add("over");
}));
["dragleave", "drop"].forEach(ev => dz.addEventListener(ev, (e) => e.preventDefault()));
dz.addEventListener("drop", (e) => {
  dz.classList.remove("over");
  const f = e.dataTransfer.files[0];
  if (f) {
    const reader = new FileReader();
    reader.onload = () => {
      $("#titles").value = reader.result;
      $("#btn-add").click();
    };
    reader.readAsText(f);
  }
});

// ---------- step 2: search results ----------
function fmtTime(sec) {
  if (sec == null) return "";
  return `${Math.floor(sec / 60)}:${String(sec % 60).padStart(2, "0")}`;
}

function renderResults(results) {
  const wrap = $("#search-results");
  wrap.innerHTML = "";
  selections = [];
  searchGroups = [];
  results.forEach((g, gi) => {
    const div = document.createElement("div");
    div.className = "group";
    if (g.error) {
      div.innerHTML = `<div class="err">No results for “${esc(g.query)}”</div>`;
      wrap.appendChild(div);
      return;
    }
    div.innerHTML += `<div class="q">${esc(g.query)}</div>`;
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
        <span class="d">${fmtTime(c.duration)}</span>`;
      row.addEventListener("click", () => {
        [...row.parentNode.querySelectorAll(".cand")].forEach(x => x.classList.remove("sel"));
        row.classList.add("sel");
        row.querySelector("input").checked = true;
      });
      div.appendChild(row);
    });
    wrap.appendChild(div);
    searchGroups.push({ query: g.query, candidates: g.candidates });
  });
  const total = searchGroups.length;
  $("#search-count").textContent = `(${total} songs)`;
  $("#btn-download").classList.remove("hidden");
  if (total) $("#btn-download").textContent = `Download selected (${total})`;
}

// ---------- step 3: download ----------
$("#btn-download").addEventListener("click", async () => {
  const groups = [...document.querySelectorAll(".group")].filter(g => !g.querySelector(".err"));
  const picked = groups.map((g, gi) => {
    const idx = [...g.querySelectorAll("input")].findIndex(i => i.checked);
    const c = searchGroups[gi].candidates[idx] || searchGroups[gi].candidates[0];
    return {
      id: c.id,
      title: c.title,
      artist: c.artist,
      album: c.album || "",
      duration: c.duration,
    };
  });
  await startDownload(picked);
});

async function startDownload(items) {
  $("#s-dl").classList.remove("hidden");
  $("#dl-items").innerHTML = items.map(it =>
    `<div class="dl-item" data-id="${it.id}"><span>${esc(it.title)}</span><span class="st">queued</span></div>`).join("");
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
  $("#dl-status").textContent = j.current ? `Working on “${j.current}”…` : "Waiting…";
  j.items.forEach(it => {
    const row = document.querySelector(`.dl-item[data-id="${it.id}"]`);
    if (row) {
      row.querySelector(".st").textContent = it.status;
      row.querySelector(".st").className = "st " + it.status;
    }
  });
  if (j.state === "working") {
    setTimeout(pollDownload, 800);
  } else if (j.state === "done") {
    $("#dl-status").textContent = "All done!";
    showBurn();
  } else {
    $("#dl-status").textContent = "Download failed: " + (j.error || "");
  }
}

// ---------- step 4: burn ----------
async function loadDrives() {
  const d = await api("/api/drives");
  const sel = $("#drive");
  sel.innerHTML = "";
  if (!d.ok) {
    sel.innerHTML = `<option value="">${esc(d.error || "No drive found")}</option>`;
    $("#disc-info").textContent = d.error || "No drive found";
    return;
  }
  d.drives.forEach(x => {
    const o = document.createElement("option");
    o.value = x.letter;
    o.textContent = `${x.letter} — ${x.description}`;
    sel.appendChild(o);
  });
  if (d.mediaLoaded === false) {
    $("#disc-info").textContent = "No disc in the drive — insert a blank CD-R.";
  } else if (d.blank === false) {
    $("#disc-info").textContent = "Disc in drive is not blank — insert a blank CD-R.";
  } else {
    $("#disc-info").textContent = "";
  }
}

async function showBurn() {
  $("#s-burn").classList.remove("hidden");
  loadDrives();
  const j = await api(`/api/status/${jobId}`);
  let secs = 0;
  j.items.forEach(it => {
    if (it.duration) secs += it.duration;
  });
  $("#time-info").textContent =
    `Total play time: ${Math.floor(secs / 60)}:${String(secs % 60).padStart(2, "0")}` +
    (secs > 74 * 60 ? " — over 74 min, check disc capacity." : "");
}

$("#btn-burn").addEventListener("click", async () => {
  const j = await api(`/api/status/${jobId}`);
  const ids = j.items.map(it => it.id);
  const res = await api("/api/burn", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ drive: $("#drive").value, mode: $("#mode").value,
                           speed_kb: parseInt($("#speed").value, 10), ids }),
  });
  if (res.error) { alert(res.error); return; }
  burnId = res.burn;
  $("#btn-burn").disabled = true;
  $("#burn-log").classList.remove("hidden");
  pollBurn();
});

async function pollBurn() {
  const b = await api(`/api/burnstatus/${burnId}`);
  $("#burn-log").textContent = b.log || b.message;
  if (b.state === "working") {
    setTimeout(pollBurn, 1000);
  } else {
    $("#btn-burn").disabled = false;
    if (b.state === "done") {
      $("#burn-log").textContent += "\n✔ Disc burned successfully.";
    } else {
      $("#burn-log").textContent += "\n✖ " + b.message;
    }
  }
}

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, c =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}