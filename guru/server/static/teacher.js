/* Offline Guru teacher console. Vanilla JS, no external dependencies. */
"use strict";

const T = {
  token: localStorage.getItem("guru.teacher.token") || "",
  sessionId: localStorage.getItem("guru.teacher.session") || "",
  session: null,
  segments: new Map(),
  ws: null,
  retry: 0,
  summary: null,
  quiz: null,
  healthTimer: null,
};

const $ = (id) => document.getElementById(id);

function toast(msg) {
  const t = $("toast");
  t.textContent = msg;
  t.classList.remove("hidden");
  clearTimeout(toast._t);
  toast._t = setTimeout(() => t.classList.add("hidden"), 3000);
}

function fmtTime(sec) {
  const s = Math.max(0, Math.round(sec || 0));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

function setConn(kind, text) {
  $("connDot").className = "dot " + kind;
  $("connText").textContent = text;
}

async function api(path, opts = {}) {
  const res = await fetch(path, {
    ...opts,
    headers: {
      "Content-Type": "application/json",
      "X-Teacher-Token": T.token,
      ...(opts.headers || {}),
    },
  });
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail || detail; } catch (_) {}
    if (res.status === 401 || res.status === 403) localStorage.removeItem("guru.teacher.token");
    throw new Error(detail);
  }
  return res.json();
}

/* ------------------------------- session ------------------------------- */
$("createBtn").onclick = async () => {
  $("createBtn").disabled = true;
  try {
    const data = await api("/api/teacher/session", {
      method: "POST",
      body: JSON.stringify({
        title: $("titleInput").value.trim() || "Class lecture",
        source_lang: $("srcLang").value,
        target_lang: null,
      }),
    });
    T.token = data.teacher_token;
    T.sessionId = data.session.id;
    localStorage.setItem("guru.teacher.token", T.token);
    localStorage.setItem("guru.teacher.session", T.sessionId);
    await loadRoom();
    connectWs();
  } catch (err) {
    $("setupErr").textContent = err.message;
  } finally {
    $("createBtn").disabled = false;
  }
};

async function loadRoom() {
  if (!T.token || !T.sessionId) return false;
  try {
    const data = await api(`/api/teacher/session/${T.sessionId}`);
    T.session = data.session;
    $("setupView").classList.add("hidden");
    $("roomView").classList.remove("hidden");
    $("roomTitle").textContent = data.session.title;
    $("classCode").textContent = data.session.code;
    $("joinUrl").textContent = data.join_url;
    $("qrImg").src = `/qr.svg?data=${encodeURIComponent(data.join_url)}`;
    $("roomMeta").textContent = `${data.session.source_lang.toUpperCase()} → ${data.session.target_lang.toUpperCase()} · সেশন ${data.session.id}`;
    $("segCount").textContent = `${data.segments} অংশ`;
    renderStudents(data.students || []);
    renderHealth(data);
    updateControls(data.pipeline?.status || "idle");
    await loadSegments();
    await loadMaterials();
    await loadDocuments();
    startHealthPolling();
    return true;
  } catch (err) {
    toast(err.message);
    return false;
  }
}

function updateControls(status) {
  const running = status === "running";
  const paused = status === "paused";
  const idle = status === "idle";
  $("startMicBtn").classList.toggle("hidden", !idle);
  $("startWavBtn").classList.toggle("hidden", !idle);
  $("pauseBtn").classList.toggle("hidden", !running);
  $("resumeBtn").classList.toggle("hidden", !paused);
  $("stopBtn").classList.toggle("hidden", idle);
  setConn(running ? "live" : paused ? "warn" : "off",
    running ? "লাইভ" : paused ? "বিরতিতে" : "নিষ্ক্রিয়");
}

async function control(path, body) {
  try {
    const data = await api(`/api/teacher/session/${T.sessionId}/${path}`, {
      method: "POST",
      body: body ? JSON.stringify(body) : undefined,
    });
    updateControls(data.pipeline?.status || "running");
    if (path === "start" && data.warnings?.length) data.warnings.forEach(toast);
    if (path === "start" && data.pipeline?.status !== "running") toast("পাইপলাইন শুরু হয়নি");
    return data;
  } catch (err) {
    toast(err.message);
  }
}

function hotwords() {
  const el = $("hotwordsInput");
  return el ? el.value.trim() : "";
}

$("startMicBtn").onclick = async () => { await control("start", { capture: "mic", hotwords: hotwords() }); pollHealth(); };
$("startWavBtn").onclick = async () => {
  const path = prompt("১৬ kHz mono WAV ফাইলের পূর্ণ পথ:", "data/demo/lecture.wav");
  if (!path) return;
  await control("start", { capture: "wav", wav_path: path, speed: 1.0, hotwords: hotwords() });
  pollHealth();
};
$("pauseBtn").onclick = () => control("pause");
$("resumeBtn").onclick = () => control("resume");
$("stopBtn").onclick = async () => { await control("stop"); await loadMaterials(); pollHealth(); };

/* ------------------------------ segments ------------------------------ */
async function loadSegments() {
  const data = await api(`/api/teacher/session/${T.sessionId}/segments?after_seq=-1`);
  (data.segments || []).forEach((s) => T.segments.set(s.id, s));
  renderTranscript();
}

function mergeSegment(seg) {
  const prev = T.segments.get(seg.id) || {};
  const changed = prev.bn !== seg.bn || prev.en !== seg.en;
  T.segments.set(seg.id, { ...prev, ...seg });
  renderTranscript(seg.id, changed);
}

function renderTranscript(highlightId, flash) {
  const list = $("transcript");
  const items = [...T.segments.values()].sort((a, b) => (a.seq || 0) - (b.seq || 0));
  $("emptyTranscript").style.display = items.length ? "none" : "block";
  $("segCount").textContent = `${items.length} অংশ`;
  const atBottom = list.scrollHeight - list.scrollTop - list.clientHeight < 120;
  list.replaceChildren();

  for (const seg of items) {
    const el = document.createElement("div");
    el.className = "seg" + (seg.id === highlightId && flash ? " updated" : "");
    const meta = document.createElement("div");
    meta.className = "row";
    const t = document.createElement("time");
    t.textContent = `${fmtTime(seg.start_s)}–${fmtTime(seg.end_s)}`;
    meta.appendChild(t);
    const conf = document.createElement("span");
    conf.className = "pill";
    conf.style.fontSize = ".72rem";
    conf.textContent = `conf ${Math.round((seg.confidence || 0) * 100)}%`;
    meta.appendChild(conf);
    if (seg.e2e_ms) {
      const p = document.createElement("span");
      p.className = "pill";
      p.style.fontSize = ".72rem";
      p.textContent = `⚡ ${(seg.e2e_ms / 1000).toFixed(1)}s`;
      meta.appendChild(p);
    }
    if (seg.numbers_preserved === false) {
      const w = document.createElement("span");
      w.className = "pill warn";
      w.style.fontSize = ".72rem";
      w.textContent = "সংখ্যা হারিয়েছে";
      meta.appendChild(w);
    }
    const source = document.createElement("div");
    source.textContent = seg.source_text || seg.en || seg.bn || "";
    const translated = document.createElement("div");
    translated.style.marginTop = ".2rem";
    translated.textContent = seg.translated_text || (seg.target_language === "en" ? seg.en : seg.bn) || "…";
    const tools = document.createElement("div");
    tools.className = "tools";
    const edit = document.createElement("button");
    edit.className = "ghost small";
    edit.textContent = "✎ সম্পাদনা";
    edit.onclick = () => editSegment(seg);
    tools.appendChild(edit);
    if (seg.audio_url) {
      const play = document.createElement("button");
      play.className = "ghost small";
      play.textContent = "🔊";
      play.onclick = () => new Audio(`/api/teacher/audio/${encodeURIComponent(seg.id)}?token=${encodeURIComponent(T.token)}`).play().catch(() => {});
      tools.appendChild(play);
    }
    el.appendChild(meta);
    el.appendChild(source);
    el.appendChild(translated);
    el.appendChild(tools);
    list.appendChild(el);
  }
  if (atBottom) list.scrollTop = list.scrollHeight;
}

function editSegment(seg) {
  const en = prompt("English transcript:", seg.en || "");
  if (en === null) return;
  const bn = prompt("বাংলা অনুবাদ:", seg.bn || "");
  if (bn === null) return;
  api(`/api/teacher/segment/${seg.id}`, {
    method: "PATCH",
    body: JSON.stringify({ en, bn }),
  }).then((data) => {
    mergeSegment(data.segment);
    toast("সংরক্ষিত হয়েছে");
  }).catch((e) => toast(e.message));
}

/* ------------------------------ materials ----------------------------- */
async function loadMaterials() {
  for (const kind of ["summary", "quiz"]) {
    try {
      const m = await api(`/api/teacher/session/${T.sessionId}/materials/${kind}`);
      if (kind === "summary") { T.summary = m; renderSummary(); }
      else { T.quiz = m; renderQuiz(); }
    } catch (_) {
      if (kind === "summary") { T.summary = null; renderSummary(); }
      else { T.quiz = null; renderQuiz(); }
    }
  }
  updateApproveButtons();
}

function updateApproveButtons() {
  $("approveSummaryBtn").disabled = !T.summary || T.summary.status === "approved";
  $("approveQuizBtn").disabled = !T.quiz || T.quiz.status === "approved";
  $("approveSummaryBtn").textContent = T.summary?.status === "approved" ? "✓ অনুমোদিত" : "অনুমোদন দিন";
  $("approveQuizBtn").textContent = T.quiz?.status === "approved" ? "✓ অনুমোদিত" : "অনুমোদন দিন";
}

function renderSummary() {
  const box = $("summaryBox");
  box.replaceChildren();
  if (!T.summary) { box.textContent = "এখনো তৈরি হয়নি। (ক্লাস চলাকালে স্বয়ংক্রিয়ভাবে খসড়া তৈরি হয়)"; return; }
  const c = T.summary.content;
  const badge = document.createElement("span");
  badge.className = "pill " + (T.summary.status === "approved" ? "ok" : "warn");
  badge.textContent = T.summary.status === "approved" ? "অনুমোদিত" : "খসড়া";
  box.appendChild(badge);
  const list = document.createElement("div");
  list.style.marginTop = ".5rem";
  (c.summary_bn || []).forEach((s) => {
    const p = document.createElement("p");
    const label = s.page ? `পৃষ্ঠা ${s.page}` : fmtTime(s.start_s);
    p.textContent = `[${label}] ${s.text}`;
    list.appendChild(p);
  });
  box.appendChild(list);
  if ((c.sources || []).length) {
    const src = document.createElement("div");
    src.className = "muted";
    src.style.fontSize = ".8rem";
    src.textContent = "সূত্র: " + c.sources.map((s) => `${s.document} · পৃষ্ঠা ${s.page}`).join("; ");
    box.appendChild(src);
  }
  if ((c.key_terms || []).length) {
    const terms = document.createElement("div");
    terms.className = "row";
    c.key_terms.forEach((k) => {
      const pill = document.createElement("span");
      pill.className = "pill";
      pill.textContent = `${k.bn || k.en}`;
      pill.title = k.en;
      terms.appendChild(pill);
    });
    box.appendChild(terms);
  }
  if (c.insufficient_content) {
    const n = document.createElement("p");
    n.className = "muted";
    n.textContent = "যথেষ্ট ট্রান্সক্রিপ্ট নেই — আরও লেকচার দরকার।";
    box.appendChild(n);
  }
}

function renderQuiz() {
  const box = $("quizBox");
  box.replaceChildren();
  if (!T.quiz) { box.textContent = "এখনো তৈরি হয়নি।"; return; }
  const badge = document.createElement("span");
  badge.className = "pill " + (T.quiz.status === "approved" ? "ok" : "warn");
  badge.textContent = T.quiz.status === "approved" ? "অনুমোদিত (শিক্ষার্থীরা দেখতে পাচ্ছে)" : "খসড়া (শিক্ষার্থীরা দেখতে পাবে না)";
  box.appendChild(badge);
  const qs = T.quiz.content.questions || [];
  if (!qs.length) {
    const p = document.createElement("p");
    p.className = "muted";
    p.textContent = "ট্রান্সক্রিপ্টে যথেষ্ট পরিষ্কার তথ্য নেই, তাই প্রশ্ন তৈরি হয়নি।";
    box.appendChild(p);
  }
  qs.forEach((q, i) => {
    const d = document.createElement("div");
    d.style.margin = ".7rem 0";
    d.style.borderTop = "1px solid #2a3441";
    d.style.paddingTop = ".5rem";
    const qq = document.createElement("div");
    qq.textContent = `${i + 1}. ${q.question_en}`;
    d.appendChild(qq);
    const bn = document.createElement("div");
    bn.className = "muted";
    bn.textContent = q.question_bn || "";
    d.appendChild(bn);
    if (q.type === "mcq") {
      q.options.forEach((o) => {
        const row = document.createElement("div");
        row.style.fontSize = ".9rem";
        row.textContent = `${o.id}. ${o.en}${o.id === q.correct_option_id ? "  ✓" : ""}`;
        row.style.color = o.id === q.correct_option_id ? "#7ee787" : "";
        d.appendChild(row);
      });
    } else {
      const ans = document.createElement("div");
      ans.style.fontSize = ".9rem";
      ans.style.color = "#7ee787";
      ans.textContent = `উত্তর: ${q.answer_text_en}`;
      d.appendChild(ans);
      const rubric = document.createElement("div");
      rubric.className = "muted";
      rubric.style.fontSize = ".8rem";
      rubric.textContent = `রুব্রিক: ${(q.rubric_terms || []).join(", ")}`;
      d.appendChild(rubric);
    }
    const ev = document.createElement("div");
    ev.className = "muted";
    ev.style.fontSize = ".78rem";
    const src = q.evidence?.source;
    ev.textContent = src
      ? `সূত্র: ${src.document} · পৃষ্ঠা ${src.page} — “${(q.evidence?.quote_en || "").slice(0, 90)}…”`
      : `প্রমাণ: ${fmtTime(q.evidence?.start_s || 0)} — “${(q.evidence?.quote_en || "").slice(0, 90)}…”`;
    d.appendChild(ev);
    box.appendChild(d);
  });
}

$("genSummaryBtn").onclick = async () => {
  $("genSummaryBtn").disabled = true;
  try {
    await api(`/api/teacher/session/${T.sessionId}/summary`, { method: "POST" });
    await loadMaterials();
    toast("সারসংক্ষেপ তৈরি হয়েছে");
  } catch (e) { toast(e.message); } finally { $("genSummaryBtn").disabled = false; }
};

$("genQuizBtn").onclick = async () => {
  $("genQuizBtn").disabled = true;
  try {
    await api(`/api/teacher/session/${T.sessionId}/quiz`, {
      method: "POST",
      body: JSON.stringify({ count: Number($("quizCount").value), include_short: true }),
    });
    await loadMaterials();
    toast("কুইজ খসড়া তৈরি হয়েছে");
  } catch (e) { toast(e.message); } finally { $("genQuizBtn").disabled = false; }
};

async function approve(kind) {
  try {
    await api(`/api/teacher/session/${T.sessionId}/materials/${kind}`, {
      method: "PATCH",
      body: JSON.stringify({ status: "approved" }),
    });
    await loadMaterials();
    toast(kind === "quiz" ? "কুইজ শিক্ষার্থীদের জন্য খুলে দেওয়া হয়েছে" : "সারসংক্ষেপ অনুমোদিত");
  } catch (e) { toast(e.message); }
}
$("approveSummaryBtn").onclick = () => approve("summary");
$("approveQuizBtn").onclick = () => approve("quiz");

/* ------------------------------ documents ----------------------------- */
async function loadDocuments() {
  try {
    const data = await api(`/api/teacher/session/${T.sessionId}/documents`);
    const docs = data.documents || [];
    $("docCount").textContent = `${docs.length} ফাইল`;
    const box = $("docBox");
    box.replaceChildren();
    if (!docs.length) {
      box.textContent = "এখনো কোনো উপকরণ নেই।";
      return;
    }
    docs.forEach((doc) => {
      const row = document.createElement("div");
      row.className = "row";
      row.style.justifyContent = "space-between";
      row.style.fontSize = ".88rem";
      const label = document.createElement("span");
      label.textContent = `${doc.name} · ${doc.pages} পৃষ্ঠা · ${doc.chunks} অংশ`;
      const del = document.createElement("button");
      del.className = "ghost small";
      del.textContent = "✕";
      del.title = "এই উপকরণ মুছুন";
      del.onclick = () => deleteDocument(doc);
      row.appendChild(label);
      row.appendChild(del);
      box.appendChild(row);
    });
  } catch (e) { toast(e.message); }
}

async function uploadDocument() {
  const input = $("docInput");
  const file = input.files && input.files[0];
  if (!file) { toast("আগে একটি ফাইল নির্বাচন করুন"); return; }
  const btn = $("docUploadBtn");
  btn.disabled = true;
  try {
    // Raw body, not multipart: the server reads request.body() directly.
    const res = await fetch(
      `/api/teacher/session/${T.sessionId}/documents?name=${encodeURIComponent(file.name)}`,
      {
        method: "POST",
        headers: { "X-Teacher-Token": T.token, "Content-Type": "application/octet-stream" },
        body: file,
      }
    );
    if (!res.ok) {
      let detail = res.statusText;
      try { detail = (await res.json()).detail || detail; } catch (_) {}
      throw new Error(detail);
    }
    const data = await res.json();
    toast(data.duplicate
      ? "এই ফাইল আগেই যুক্ত হয়েছে"
      : `${data.document.name}: ${data.document.pages} পৃষ্ঠা যুক্ত হয়েছে`);
    input.value = "";
    await loadDocuments();
  } catch (e) {
    toast(e.message);
  } finally {
    btn.disabled = false;
  }
}

async function deleteDocument(doc) {
  if (!confirm(`${doc.name} মুছে ফেলবেন?`)) return;
  try {
    await api(`/api/teacher/session/${T.sessionId}/documents/${doc.id}?confirm=true`, { method: "DELETE" });
    await loadDocuments();
    toast("মুছে ফেলা হয়েছে");
  } catch (e) { toast(e.message); }
}

async function searchDocuments() {
  const q = $("docSearchInput").value.trim();
  const box = $("docSearchBox");
  if (!q) { box.textContent = ""; return; }
  try {
    const data = await api(
      `/api/teacher/session/${T.sessionId}/documents/search?q=${encodeURIComponent(q)}&k=5`
    );
    box.replaceChildren();
    if (!data.hits.length) { box.textContent = "কিছু পাওয়া যায়নি।"; return; }
    data.hits.forEach((hit) => {
      const p = document.createElement("p");
      p.textContent = `${hit.document} · পৃষ্ঠা ${hit.page} (${hit.score}) — ${hit.text.slice(0, 120)}…`;
      box.appendChild(p);
    });
  } catch (e) { toast(e.message); }
}

$("docUploadBtn").onclick = uploadDocument;
$("docSearchBtn").onclick = searchDocuments;

/* ------------------------------- health ------------------------------- */
const healthCache = { data: null };

function renderHealth(data) {
  const box = $("healthBox");
  box.replaceChildren();
  const m = document.createElement("div");
  m.className = "metrics";
  const metrics = data.metrics || {};
  const e2e = metrics.e2e_ms || {};
  const tr = metrics.translate_ms || {};
  const asr = metrics.asr_ms || {};
  [
    ["e2e p50", e2e.p50 ? `${e2e.p50} ms` : "—"],
    ["e2e p95", e2e.p95 ? `${e2e.p95} ms` : "—"],
    ["ASR p50", asr.p50 ? `${asr.p50} ms` : "—"],
    ["অনুবাদ p50", tr.p50 ? `${tr.p50} ms` : "—"],
  ].forEach(([label, val]) => {
    const d = document.createElement("div");
    d.className = "metric";
    const b = document.createElement("b");
    b.textContent = val;
    const s = document.createElement("span");
    s.textContent = label;
    d.appendChild(b);
    d.appendChild(s);
    m.appendChild(d);
  });
  box.appendChild(m);

  const models = data.models || {};
  const row = document.createElement("div");
  row.className = "row";
  row.style.marginTop = ".5rem";
  Object.entries(models).forEach(([name, st]) => {
    const p = document.createElement("span");
    p.className = "pill " + (st.loaded ? "ok" : "bad");
    p.textContent = st.loaded ? `${name} ✓ ${st.ms} ms` : `${name} ✗`;
    p.title = st.error || "";
    row.appendChild(p);
  });
  box.appendChild(row);

  const net = data.network || {};
  const nw = document.createElement("div");
  nw.className = "muted";
  nw.style.fontSize = ".85rem";
  nw.style.marginTop = ".4rem";
  const iface = (net.interfaces || []).map((i) => `${i.name} ${i.ip}`).join(" · ") || "কোনো ইন্টারফেস নেই";
  nw.textContent = `${net.hotspot_active ? "হটস্পট সক্রিয়" : "হটস্পট পাওয়া যায়নি"} · ${iface} · ইন্টারনেট: ${net.online ? "আছে (ব্যবহৃত হয় না)" : "নেই — অফলাইন মোড"}`;
  box.appendChild(nw);

  const warnBox = $("warnBox");
  warnBox.replaceChildren();
  (data.warnings || []).forEach((w) => {
    const p = document.createElement("p");
    p.textContent = `⚠ ${w}`;
    warnBox.appendChild(p);
  });
  healthCache.data = data;
}

function renderStudents(students) {
  const body = $("studentRows");
  body.replaceChildren();
  $("studentCount").textContent = String(students.length);
  students.forEach((s) => {
    const tr = document.createElement("tr");
    const n = document.createElement("td");
    n.textContent = s.name;
    const st = document.createElement("td");
    const pill = document.createElement("span");
    pill.className = "pill " + (s.online ? "ok" : "");
    pill.textContent = s.online ? "অনলাইন" : "নিষ্ক্রিয়";
    st.appendChild(pill);
    tr.appendChild(n);
    tr.appendChild(st);
    body.appendChild(tr);
  });
}

async function pollHealth() {
  try {
    const data = await api(`/api/teacher/session/${T.sessionId}`);
    renderHealth(data);
    $("segCount").textContent = `${data.segments} অংশ`;
    renderStudents(data.students || []);
    updateControls(data.pipeline?.status || "idle");
  } catch (_) {}
}

function startHealthPolling() {
  clearInterval(T.healthTimer);
  T.healthTimer = setInterval(pollHealth, 5000);
}

/* ------------------------------- answers ------------------------------ */
async function loadAnswers() {
  try {
    const data = await api(`/api/teacher/session/${T.sessionId}/answers`);
    const box = $("answersBox");
    box.replaceChildren();
    if (!data.answers.length) {
      const p = document.createElement("p");
      p.className = "muted";
      p.textContent = "এখনো কোনো উত্তর জমা পড়েনি।";
      box.appendChild(p);
      return;
    }
    const table = document.createElement("table");
    const thead = document.createElement("thead");
    thead.innerHTML = "<tr><th>শিক্ষার্থী</th><th>প্রশ্ন</th><th>উত্তর</th><th>ফল</th><th></th></tr>";
    table.appendChild(thead);
    const tb = document.createElement("tbody");
    data.answers.forEach((a) => {
      const tr = document.createElement("tr");
      [ a.student_name, a.question_id, a.answer.slice(0, 60),
        a.needs_review ? "যাচাই দরকার" : a.is_correct === 1 ? "সঠিক" : a.is_correct === 0 ? "ভুল" : "—"
      ].forEach((v) => {
        const td = document.createElement("td");
        td.textContent = v;
        tr.appendChild(td);
      });
      const td = document.createElement("td");
      const ok = document.createElement("button");
      ok.className = "ghost small";
      ok.textContent = "✓";
      ok.title = "সঠিক হিসেবে চিহ্নিত করুন";
      ok.onclick = () => overrideAnswer(a.id, true);
      const no = document.createElement("button");
      no.className = "ghost small";
      no.textContent = "✗";
      no.title = "ভুল হিসেবে চিহ্নিত করুন";
      no.onclick = () => overrideAnswer(a.id, false);
      td.appendChild(ok);
      td.appendChild(no);
      tr.appendChild(td);
      tb.appendChild(tr);
    });
    table.appendChild(tb);
    box.appendChild(table);
  } catch (e) { toast(e.message); }
}

async function overrideAnswer(id, correct) {
  try {
    await api(`/api/teacher/answer/${id}/override`, {
      method: "POST",
      body: JSON.stringify({ is_correct: correct }),
    });
    toast("সংশোধন সংরক্ষিত");
    loadAnswers();
  } catch (e) { toast(e.message); }
}

$("refreshAnswersBtn").onclick = loadAnswers;
$("exportBtn").onclick = async () => {
  try {
    const res = await fetch(`/api/teacher/session/${T.sessionId}/export`, {
      headers: { "X-Teacher-Token": T.token },
    });
    if (!res.ok) throw new Error("এক্সপোর্ট ব্যর্থ");
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `offline-guru-${T.sessionId}.json`;
    a.click();
    URL.revokeObjectURL(url);
  } catch (e) { toast(e.message); }
};
$("endBtn").onclick = async () => {
  if (!confirm("ক্লাস শেষ করবেন? শিক্ষার্থীরা আর যোগ দিতে পারবে না।")) return;
  try { await api(`/api/teacher/session/${T.sessionId}/end`, { method: "POST" }); toast("ক্লাস শেষ"); }
  catch (e) { toast(e.message); }
};
$("deleteBtn").onclick = async () => {
  if (!confirm("এই ক্লাসের সব ট্রান্সক্রিপ্ট, উত্তর ও অডিও স্থায়ীভাবে মুছে যাবে। নিশ্চিত?")) return;
  try {
    await api(`/api/teacher/session/${T.sessionId}?confirm=true`, { method: "DELETE" });
    localStorage.removeItem("guru.teacher.session");
    localStorage.removeItem("guru.teacher.token");
    location.reload();
  } catch (e) { toast(e.message); }
};

/* ------------------------------ websocket ----------------------------- */
function connectWs() {
  if (!T.token || !T.sessionId) return;
  if (T.ws && T.ws.readyState <= 1) return;
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(
    `${proto}://${location.host}/ws/teacher?session_id=${encodeURIComponent(T.sessionId)}&token=${encodeURIComponent(T.token)}`
  );
  T.ws = ws;
  ws.onopen = () => { T.retry = 0; setConn("live", "লাইভ"); };
  ws.onmessage = (ev) => {
    let msg;
    try { msg = JSON.parse(ev.data); } catch (_) { return; }
    if (msg.type === "ping") { try { ws.send("ping"); } catch (_) {} return; }
    if (msg.type === "transcript.partial") {
      $("partialCard").style.display = "block";
      $("partialText").textContent = msg.source_text || msg.text || msg.en || "";
      return;
    }
    if (msg.type === "transcript.final") {
      $("partialCard").style.display = "none";
      mergeSegment({ ...msg.segment, bn: "" });
      return;
    }
    if (msg.type === "segment") { mergeSegment(msg.segment); return; }
    if (msg.type === "segment.updated") { mergeSegment(msg.segment); return; }
    if (msg.type === "audio.ready") {
      mergeSegment({ id: msg.segment_id, audio_url: msg.audio_url, tts_ms: msg.tts_ms });
      return;
    }
    if (msg.type === "materials.updated") { loadMaterials(); return; }
    if (msg.type === "answer.submitted") { loadAnswers(); return; }
    if (msg.type === "classroom") { updateControls(msg.status); }
  };
  ws.onclose = () => {
    setConn("warn", "পুনঃসংযোগ…");
    const delay = Math.min(15000, 800 * Math.pow(1.6, T.retry++));
    setTimeout(connectWs, delay);
  };
};

/* -------------------------------- startup ----------------------------- */
(async () => {
  const ok = await loadRoom();
  if (ok) {
    connectWs();
    loadAnswers();
  } else {
    $("setupView").classList.remove("hidden");
  }
})();
