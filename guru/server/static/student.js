/* Offline Guru student client. Vanilla JS, no external dependencies.
 * All user-supplied content is rendered with textContent (never innerHTML).
 */
"use strict";

const state = {
  token: localStorage.getItem("guru.student.token") || "",
  student: null,
  session: null,
  segments: new Map(),
  quiz: null,
  feedback: null,
  summary: null,
  // Autoplay permission must be granted in each document via a browser gesture.
  audioEnabled: false,
  audioContext: null,
  activeSource: null,
  audioStatus: "disabled",
  ws: null,
  retry: 0,
  audioQueue: [],
  manualQueue: [],
  audioBusy: false,
  played: new Set(),
  queued: new Set(),
  nextPlaySeq: 1,
  maxObservedSeq: 0,
  pendingAudio: new Map(),
  audioWaitTimer: null,
};

const $ = (id) => document.getElementById(id);

function toast(msg) {
  const t = $("toast");
  t.textContent = msg;
  t.classList.remove("hidden");
  clearTimeout(toast._t);
  toast._t = setTimeout(() => t.classList.add("hidden"), 2600);
}

function fmtTime(sec) {
  const s = Math.max(0, Math.round(sec || 0));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

function setConnection(kind, text) {
  const dot = $("connDot");
  dot.className = "dot " + (kind === "live" ? "live" : kind === "warn" ? "warn" : "off");
  $("connText").textContent = text;
}

async function api(path, opts = {}) {
  const res = await fetch(path, {
    ...opts,
    headers: { "Content-Type": "application/json", ...(opts.headers || {}) },
  });
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail || detail; } catch (_) {}
    throw new Error(detail);
  }
  return res.json();
}

/* ---------------------------- join / resume ---------------------------- */
async function join(code, name) {
  const data = await api("/api/student/join", {
    method: "POST",
    body: JSON.stringify({ code, name }),
  });
  state.token = data.student.token;
  state.student = data.student;
  state.session = data.session;
  state.quiz = data.quiz;
  localStorage.setItem("guru.student.token", state.token);
  localStorage.setItem("guru.student.code", code.toUpperCase());
  data.segments.forEach((s) => { state.segments.set(s.id, s); state.maxObservedSeq = Math.max(state.maxObservedSeq, Number(s.seq || 0)); });
  state.nextPlaySeq = Math.max(1, state.maxObservedSeq + 1);
  showClassroom();
  renderTranscript();
  fetchState();
}

async function resume() {
  if (!state.token) return false;
  try {
    const data = await api(`/api/student/state?token=${encodeURIComponent(state.token)}`);
    state.student = data.student;
    state.session = data.session;
    state.quiz = data.quiz;
    state.feedback = data.feedback;
    state.summary = data.summary;
    data.segments.forEach((s) => { state.segments.set(s.id, s); state.maxObservedSeq = Math.max(state.maxObservedSeq, Number(s.seq || 0)); });
    showClassroom();
    renderTranscript();
    renderQuiz();
    renderSummary();
    renderProgress();
    resetPlayback();
    connectWs();
    return true;
  } catch (err) {
    localStorage.removeItem("guru.student.token");
    state.token = "";
    return false;
  }
}

async function fetchState() {
  if (!state.token) return;
  try {
    const data = await api(`/api/student/state?token=${encodeURIComponent(state.token)}`);
    state.quiz = data.quiz;
    state.feedback = data.feedback;
    state.summary = data.summary;
    (data.segments || []).forEach((s) => {
      const prev = state.segments.get(s.id);
      const merged = { ...prev, ...s };
      state.segments.set(s.id, merged);
      state.maxObservedSeq = Math.max(state.maxObservedSeq, Number(s.seq || 0));
      if (merged.audio_url && Number(merged.seq || 0) >= state.nextPlaySeq) queueAudio(merged);
    });
    renderTranscript();
    renderQuiz();
    renderSummary();
    renderProgress();
    if (data.live) setConnection("live", "লাইভ");
  } catch (_) {}
}

function showClassroom() {
  $("joinView").classList.add("hidden");
  $("classView").classList.remove("hidden");
  $("classTitle").textContent = state.session?.title || "শ্রেণি";
  $("classMeta").textContent = state.student ? `নাম: ${state.student.name}` : "";
  updateAudioBtn();
}

/* ------------------------------ websocket ------------------------------ */
function connectWs() {
  if (!state.token) return;
  if (state.ws && state.ws.readyState <= 1) return;
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(`${proto}://${location.host}/ws/student?token=${encodeURIComponent(state.token)}`);
  state.ws = ws;

  ws.onopen = () => {
    state.retry = 0;
    setConnection("live", "লাইভ");
  };
  ws.onmessage = (ev) => {
    let msg;
    try { msg = JSON.parse(ev.data); } catch (_) { return; }
    handleEvent(msg);
  };
  ws.onclose = () => {
    setConnection("warn", "পুনঃসংযোগ…");
    const delay = Math.min(15000, 800 * Math.pow(1.6, state.retry++));
    setTimeout(async () => {
      if (await resume()) {
        setConnection("live", "লাইভ");
      } else {
        connectWs();
      }
    }, delay);
  };
  ws.onerror = () => { try { ws.close(); } catch (_) {} };
}

function handleEvent(msg) {
  if (msg.type === "ping") {
    try { state.ws.send("ping"); } catch (_) {}
    return;
  }
  if (msg.type === "hello") {
    if (msg.live) setConnection("live", "লাইভ");
    return;
  }
  if (msg.type === "transcript.partial") {
    if (msg.session_id !== state.session?.id) return;
    $("partialCard").style.display = "block";
    $("partialText").textContent = msg.source_text || msg.text || msg.en || "";
    return;
  }
  if (msg.type === "transcript.stable") {
    if (msg.session_id !== state.session?.id) return;
    $("partialCard").style.display = "block";
    $("partialText").textContent = msg.text || "";
    return;
  }
  if (msg.type === "pipeline.status") {
    if (msg.status === "stopped") {
      setConnection("warn", "ক্লাস শেষ");
      $("partialCard").style.display = "none";
    }
    return;
  }
  if (msg.type === "pipeline.error") {
    // Never surface a stage error as a caption; the English text stays visible.
    console.warn("pipeline error", msg.stage, msg.error);
    return;
  }
  if (msg.type === "segment") {
    if (msg.session_id !== state.session?.id) return;
    $("partialCard").style.display = "none";
    mergeSegment(msg.segment);
    return;
  }
  if (msg.type === "segment.updated") {
    if (msg.session_id !== state.session?.id) return;
    mergeSegment(msg.segment);
    if (msg.segment?.audio_status === "failed") updateAudioStatus("error");
    drainAudio(); return;
  }
  if (msg.type === "audio.ready") {
    if (msg.session_id !== state.session?.id) return;
    const seg = state.segments.get(msg.segment_id);
    if (!seg) {
      state.pendingAudio.set(msg.segment_id, msg);
      // The caption may have been lost/reordered; REST will reconcile it.
      fetchState();
      return;
    }
    seg.audio_url = msg.audio_url || msg.audio_reference;
    seg.seq = Number(msg.seq || msg.sequence_number || seg.seq || 0);
    state.maxObservedSeq = Math.max(state.maxObservedSeq, seg.seq);
    seg.audio_status = "ready";
    state.segments.set(seg.id, seg);
    renderTranscript();
    queueAudio(seg);
    return;
  }
  if (msg.type === "materials.updated") {
    fetchState();
    return;
  }
  if (msg.type === "classroom") {
    if (msg.status === "stopped") {
      setConnection("warn", "ক্লাস শেষ");
      $("partialCard").style.display = "none";
    }
  }
}

function mergeSegment(seg) {
  if (!seg || !seg.id) return;
  const prev = state.segments.get(seg.id) || {};
  const merged = { ...prev, ...seg };
  const earlyAudio = state.pendingAudio.get(seg.id);
  if (earlyAudio) { merged.audio_url = earlyAudio.audio_url || earlyAudio.audio_reference; merged.seq = Number(earlyAudio.seq || earlyAudio.sequence_number || merged.seq || 0); merged.audio_status = "ready"; state.pendingAudio.delete(seg.id); }
  const seq = Number(merged.seq || merged.sequence_number || 0);
  state.maxObservedSeq = Math.max(state.maxObservedSeq, seq);
  if (!merged.seq && seq) merged.seq = seq;
  state.segments.set(merged.id, merged);
  renderTranscript();
  if (merged.audio_url && (merged.translated_text || merged.bn || merged.en)) queueAudio(merged);
}

/* ---------------------------- transcript UI ---------------------------- */
function renderTranscript() {
  const list = $("transcript");
  const items = [...state.segments.values()].sort((a, b) => (a.seq || 0) - (b.seq || 0));
  $("emptyLive").style.display = items.length ? "none" : "block";
  list.replaceChildren();
  for (const seg of items) {
    const el = document.createElement("div");
    el.className = "seg";
    el.dataset.id = seg.id;

    const meta = document.createElement("div");
    meta.className = "row";
    const time = document.createElement("time");
    time.textContent = fmtTime(seg.start_s);
    meta.appendChild(time);
    if (seg.e2e_ms) {
      const p = document.createElement("span");
      p.className = "pill";
      p.style.fontSize = ".72rem";
      p.title = `ASR ${Math.round(seg.asr_ms || 0)} ms · অনুবাদ ${Math.round(seg.translate_ms || 0)} ms · TTS ${Math.round(seg.tts_ms || 0)} ms`;
      p.textContent = `⚡ ${(seg.e2e_ms / 1000).toFixed(1)}s`;
      meta.appendChild(p);
    }
    if (seg.numbers_preserved === false) {
      const w = document.createElement("span");
      w.className = "pill warn";
      w.style.fontSize = ".72rem";
      w.textContent = "সংখ্যা যাচাই করুন";
      meta.appendChild(w);
    }

    const translated = document.createElement("div");
    translated.className = "bn";
    translated.textContent = seg.translated_text || (seg.target_language === "en" ? seg.en : seg.bn) || "…";
    const source = document.createElement("div");
    source.className = "en";
    source.textContent = seg.source_text || seg.en || seg.bn || "";

    el.appendChild(meta);
    el.appendChild(translated);
    el.appendChild(source);

    if (seg.audio_url) {
      const tools = document.createElement("div");
      tools.className = "tools";
      const play = document.createElement("button");
      play.className = "ghost small";
      play.textContent = "🔊 আবার শুনুন";
      play.onclick = () => manualReplay(seg);
      tools.appendChild(play);
      el.appendChild(tools);
    }
    list.appendChild(el);
  }
  autoScroll();
}

let userScrolled = false;
function autoScroll() {
  if (userScrolled) return;
  window.scrollTo({ top: document.body.scrollHeight, behavior: "smooth" });
}
window.addEventListener("scroll", () => {
  const nearBottom = window.scrollY + window.innerHeight > document.body.scrollHeight - 160;
  userScrolled = !nearBottom;
});

/* ------------------------------- audio -------------------------------- */
function updateAudioBtn() {
  const btn = $("audioBtn");
  btn.textContent = state.audioEnabled ? "🔊 অডিও চালু" : "🔊 শুনতে শুরু করুন";
  btn.setAttribute("aria-pressed", String(state.audioEnabled));
  btn.disabled = state.audioEnabled;
  updateAudioStatus(state.audioEnabled ? "enabled" : state.audioStatus);
}
function updateAudioStatus(kind, seq = null) {
  state.audioStatus = kind;
  const el = $("audioStatus"); if (!el) return;
  const labels = { disabled:"অডিও চালু করতে একবার “শুনতে শুরু করুন” চাপুন", enabled:"অডিও চালু", waiting:"অডিওর অপেক্ষায়…", blocked:"অডিও প্লেব্যাক ব্লক হয়েছে — আবার শুনতে শুরু করুন চাপুন", error:"অডিও ত্রুটি — পরের অংশ চালানোর চেষ্টা হবে", playing:seq == null ? "অডিও চলছে" : "অডিও চলছে · অংশ " + seq };
  el.textContent = labels[kind] || labels.error; el.dataset.state = kind;
  if (seq != null) el.dataset.playingSeq = String(seq); else delete el.dataset.playingSeq;
}
const AUDIO_GAP_WAIT_MS=1200, MAX_AUDIO_LAG_SEQUENCES=8, MAX_QUEUED_AUDIO=8;
function queueAudio(seg) {
  if (!state.audioEnabled || !seg || !seg.audio_url) return;
  const seq=Number(seg.seq || seg.sequence_number || 0);
  if (!seq || seq<state.nextPlaySeq || state.played.has(seg.id) || state.queued.has(seg.id)) return;
  const floor=state.maxObservedSeq-MAX_AUDIO_LAG_SEQUENCES; if(seq<floor)return;
  state.audioQueue=state.audioQueue.filter(x=>{const keep=Number(x.seq||0)>=floor;if(!keep)state.queued.delete(x.id);return keep;});
  if(state.nextPlaySeq<floor)state.nextPlaySeq=floor;
  state.audioQueue.push(seg);state.queued.add(seg.id);state.audioQueue.sort((a,b)=>Number(a.seq||0)-Number(b.seq||0));
  while(state.audioQueue.length>MAX_QUEUED_AUDIO){const x=state.audioQueue.shift();state.queued.delete(x.id);state.nextPlaySeq=Math.max(state.nextPlaySeq,Number(x.seq||0)+1);} drainAudio();
}
function resetPlayback(){state.audioQueue=[];state.manualQueue=[];state.queued.clear();state.pendingAudio.clear();state.maxObservedSeq=Math.max(0,...[...state.segments.values()].map(s=>Number(s.seq||0)));state.nextPlaySeq=Math.max(1,state.maxObservedSeq+1);if(state.audioWaitTimer){clearTimeout(state.audioWaitTimer);state.audioWaitTimer=null;}}
/* Skip the backlog when audio is switched on, but keep the newest observed
 * segment eligible: its audio often arrives just after the tap. */
function startAtLiveEdge(){
  const edge=Math.max(0,state.maxObservedSeq||0);
  state.nextPlaySeq=Math.max(1,edge);
  if(!edge)return;
  const current=[...state.segments.values()].find(s=>Number(s.seq||0)===edge);
  if(current&&current.audio_url)queueAudio(current);
}
function skipKnownUnavailable(){while([...state.segments.values()].some(s=>Number(s.seq||0)===state.nextPlaySeq&&["failed","disabled"].includes(s.audio_status)))state.nextPlaySeq+=1;}
async function drainAudio(){
 if(state.audioBusy||!state.audioEnabled)return;skipKnownUnavailable();let item=null;
 if(state.audioQueue.length){const h=state.audioQueue[0],seq=Number(h.seq||0);if(seq<state.nextPlaySeq){state.audioQueue.shift();state.queued.delete(h.id);return drainAudio();}
  if(seq>state.nextPlaySeq){if(state.audioWaitTimer)return;state.audioWaitTimer=setTimeout(()=>{state.audioWaitTimer=null;skipKnownUnavailable();if(state.audioQueue.length){const n=Number(state.audioQueue[0].seq||0);if(n>state.nextPlaySeq)state.nextPlaySeq=n;drainAudio();}},AUDIO_GAP_WAIT_MS);return;}
  item=state.audioQueue.shift();state.queued.delete(item.id);state.played.add(item.id);state.nextPlaySeq=seq+1;
 }else if(state.manualQueue.length)item=state.manualQueue.shift();else{if(!["blocked","error"].includes(state.audioStatus))updateAudioStatus("enabled");return;}
 state.audioBusy=true;try{await playAudio(item);if(state.audioEnabled)updateAudioStatus("enabled");}catch(err){const blocked=err&&err.name==="NotAllowedError";updateAudioStatus(blocked?"blocked":"error");if(blocked){state.audioEnabled=false;state.audioQueue=[];state.queued.clear();updateAudioBtn();}console.warn("student audio playback failed",err);}finally{state.audioBusy=false;if(state.audioEnabled)drainAudio();}
}
async function unlockAudio(){const Ctx=window.AudioContext||window.webkitAudioContext;if(!Ctx)throw Object.assign(new Error("Web Audio unavailable"),{name:"NotSupportedError"});if(!state.audioContext||state.audioContext.state==="closed")state.audioContext=new Ctx();await state.audioContext.resume();if(state.audioContext.state!=="running")throw Object.assign(new Error("Audio context suspended"),{name:"NotAllowedError"});}
async function enableAutomaticAudio(){try{await unlockAudio();state.audioEnabled=true;startAtLiveEdge();updateAudioBtn();updateAudioStatus("enabled");drainAudio();}catch(err){updateAudioStatus(err&&err.name==="NotAllowedError"?"blocked":"error");updateAudioBtn();}}
async function manualReplay(seg){try{if(!state.audioEnabled){await unlockAudio();state.audioEnabled=true;startAtLiveEdge();updateAudioBtn();}if(seg&&seg.audio_url){state.manualQueue.push(seg);drainAudio();}}catch(err){updateAudioStatus(err&&err.name==="NotAllowedError"?"blocked":"error");}}
async function playAudio(seg){const ctx=state.audioContext;if(!seg||!seg.audio_url)return;if(!ctx||ctx.state!=="running")throw Object.assign(new Error("Audio context is not unlocked"),{name:"NotAllowedError"});updateAudioStatus("waiting");const url=seg.audio_url+(seg.audio_url.includes("?")?"&":"?")+"token="+encodeURIComponent(state.token);const response=await fetch(url);if(!response.ok)throw new Error("Audio HTTP "+response.status);const buffer=await ctx.decodeAudioData((await response.arrayBuffer()).slice(0));if(!buffer||!(buffer.duration>0))throw new Error("Audio segment decoded to an empty buffer");const source=ctx.createBufferSource();source.buffer=buffer;source.connect(ctx.destination);state.activeSource=source;await new Promise((resolve,reject)=>{let settled=false,watchdog=null;const done=()=>{if(settled)return;settled=true;if(watchdog)clearTimeout(watchdog);state.activeSource=null;resolve();};const fail=(err)=>{if(settled)return;settled=true;if(watchdog)clearTimeout(watchdog);reject(err);};source.onended=done;/* A backgrounded tab can swallow `ended`; never leave the queue stuck. */watchdog=setTimeout(()=>{try{source.stop();}catch(_){}done();},Math.max(5000,buffer.duration*1000+2500));try{source.start(0);const el=$("audioStatus");el.dataset.playCount=String(Number(el.dataset.playCount||0)+1);updateAudioStatus("playing",Number(seg.seq||seg.sequence_number||0));window.dispatchEvent(new CustomEvent("guru:audio-start",{detail:{segmentId:seg.id,seq:Number(seg.seq||0)}}));}catch(err){fail(err);}});}

/* ------------------------------ summary ------------------------------- */
function renderSummary() {
  const root = $("tab-summary");
  root.replaceChildren();
  const s = state.summary;
  if (!s) {
    root.appendChild(emptyCard("শিক্ষক এখনো সারসংক্ষেপ অনুমোদন করেননি। ক্লাস চলাকালে এটি দেখা যাবে।"));
    return;
  }
  const card = document.createElement("div");
  card.className = "card";
  const h = document.createElement("h2");
  h.textContent = s.title || "সারসংক্ষেপ";
  card.appendChild(h);
  const list = document.createElement("div");
  list.className = "transcript";
  (s.summary_bn || []).forEach((item) => {
    const row = document.createElement("div");
    row.className = "seg";
    const t = document.createElement("time");
    t.textContent = fmtTime(item.start_s);
    const p = document.createElement("p");
    p.textContent = item.text;
    row.appendChild(t);
    row.appendChild(p);
    list.appendChild(row);
  });
  card.appendChild(list);
  root.appendChild(card);

  if ((s.key_terms || []).length) {
    const terms = document.createElement("div");
    terms.className = "card";
    const th = document.createElement("h3");
    th.textContent = "মূল শব্দ";
    terms.appendChild(th);
    const wrap = document.createElement("div");
    wrap.className = "row";
    s.key_terms.forEach((k) => {
      const pill = document.createElement("span");
      pill.className = "pill";
      pill.textContent = `${k.bn || k.en}${k.bn && k.en ? ` (${k.en})` : ""}`;
      wrap.appendChild(pill);
    });
    terms.appendChild(wrap);
    root.appendChild(terms);
  }
}

function emptyCard(text) {
  const c = document.createElement("div");
  c.className = "card";
  const p = document.createElement("p");
  p.className = "muted";
  p.textContent = text;
  c.appendChild(p);
  return c;
}

/* -------------------------------- quiz -------------------------------- */
function renderQuiz() {
  const root = $("tab-quiz");
  root.replaceChildren();
  if (!state.quiz || !(state.quiz.questions || []).length) {
    root.appendChild(emptyCard("শিক্ষক কুইজ অনুমোদন করলে এখানে প্রশ্ন আসবে।"));
    return;
  }
  const answers = new Map((state.feedback?.answers || []).map((a) => [a.question_id, a]));
  const head = document.createElement("div");
  head.className = "card";
  const h = document.createElement("h2");
  h.textContent = "অনুশীলন কুইজ";
  const sub = document.createElement("p");
  sub.className = "muted";
  sub.textContent = "প্রতিটি উত্তরের পরেই ব্যাখ্যা পাবেন। ভুল হলে চিন্তার কিছু নেই — আবার শুনে চেষ্টা করুন।";
  head.appendChild(h);
  head.appendChild(sub);
  root.appendChild(head);

  state.quiz.questions.forEach((q, idx) => {
    const card = document.createElement("div");
    card.className = "card";
    const num = document.createElement("small");
    num.className = "muted";
    num.textContent = `প্রশ্ন ${idx + 1} · ${q.type === "mcq" ? "বহুনির্বাচনী" : "সংক্ষিপ্ত উত্তর"}`;
    const qtext = document.createElement("p");
    qtext.style.fontSize = "1.08rem";
    qtext.textContent = q.question_bn || q.question_en;
    const qen = document.createElement("p");
    qen.className = "muted";
    qen.style.fontSize = ".85rem";
    qen.textContent = q.question_en;
    card.appendChild(num);
    card.appendChild(qtext);
    card.appendChild(qen);

    const prior = answers.get(q.id);
    let inputEl = null;
    if (q.type === "mcq") {
      const opts = document.createElement("div");
      opts.className = "options";
      q.options.forEach((o) => {
        const label = document.createElement("label");
        label.className = "option";
        const radio = document.createElement("input");
        radio.type = "radio";
        radio.name = `q-${q.id}`;
        radio.value = o.id;
        if (prior) radio.disabled = true;
        const span = document.createElement("span");
        const bn = document.createElement("div");
        bn.textContent = o.bn || o.en;
        const en = document.createElement("div");
        en.className = "muted";
        en.style.fontSize = ".82rem";
        en.textContent = o.en;
        span.appendChild(bn);
        span.appendChild(en);
        label.appendChild(radio);
        label.appendChild(span);
        opts.appendChild(label);
        inputEl = radio;
      });
      card.appendChild(opts);
    } else {
      const ta = document.createElement("textarea");
      ta.placeholder = "নিজের ভাষায় উত্তর লিখুন…";
      if (prior) ta.disabled = true;
      card.appendChild(ta);
      inputEl = ta;
    }

    const row = document.createElement("div");
    row.className = "row";
    const btn = document.createElement("button");
    btn.className = "primary";
    btn.textContent = prior ? "উত্তর দেওয়া হয়েছে" : "উত্তর দিন";
    btn.disabled = Boolean(prior);
    const fbBox = document.createElement("div");
    fbBox.style.width = "100%";
    if (prior) renderPriorFeedback(fbBox, prior, q);
    btn.onclick = async () => {
      let answer = "";
      if (q.type === "mcq") {
        const picked = card.querySelector(`input[name="q-${q.id}"]:checked`);
        if (!picked) return toast("একটি উত্তর বেছে নিন");
        answer = picked.value;
      } else {
        answer = inputEl.value.trim();
        if (answer.length < 2) return toast("সংক্ষেপে হলেও উত্তর লিখুন");
      }
      btn.disabled = true;
      try {
        const fb = await api(`/api/student/answer?token=${encodeURIComponent(state.token)}`, {
          method: "POST",
          body: JSON.stringify({ question_id: q.id, answer }),
        });
        showFeedback(fbBox, fb, q);
        const ans = [...(state.feedback?.answers || []).filter((a) => a.question_id !== q.id), {
          question_id: q.id, is_correct: fb.is_correct, score: fb.score, needs_review: fb.needs_review,
        }];
        state.feedback = { ...(state.feedback || {}), answers: ans };
        renderProgress();
      } catch (err) {
        btn.disabled = false;
        toast(err.message || "উত্তর পাঠানো যায়নি");
      }
    };
    row.appendChild(btn);
    card.appendChild(row);
    card.appendChild(fbBox);
    root.appendChild(card);
  });
}

function renderPriorFeedback(box, prior, q) {
  const grade = prior.is_correct === true ? "ok" : prior.needs_review ? "review" : "bad";
  const div = document.createElement("div");
  div.className = `feedback ${grade}`;
  div.textContent =
    prior.is_correct === true ? "✔ সঠিক" : prior.needs_review ? "◑ শিক্ষক যাচাই করবেন" : "✘ এখনো নয়";
  box.appendChild(div);
}

function showFeedback(box, fb, q) {
  box.replaceChildren();
  const grade = fb.is_correct === true ? "ok" : fb.needs_review ? "review" : "bad";
  const div = document.createElement("div");
  div.className = `feedback ${grade}`;
  const title = document.createElement("b");
  title.textContent = fb.is_correct === true ? "সঠিক! " : fb.needs_review ? "প্রায় হয়েছে " : "আবার চেষ্টা করুন ";
  const body = document.createElement("span");
  body.textContent = fb.feedback_bn || "";
  div.appendChild(title);
  div.appendChild(body);
  if (fb.answer_text_bn) {
    const ans = document.createElement("p");
    ans.textContent = `সঠিক উত্তর: ${fb.answer_text_bn}`;
    div.appendChild(ans);
  }
  const ts = document.createElement("p");
  ts.className = "muted";
  ts.style.fontSize = ".85rem";
  ts.textContent = `লেকচারের ${fmtTime(fb.evidence?.start_s || 0)} সময়ে শুনুন।`;
  div.appendChild(ts);

  // Highlight correct/wrong options for MCQs.
  if (q.type === "mcq") {
    document.querySelectorAll(`input[name="q-${q.id}"]`).forEach((r) => {
      r.disabled = true;
      const label = r.closest(".option");
      if (r.value === fb.correct_option_id) label.classList.add("correct");
      if (r.checked && r.value !== fb.correct_option_id) label.classList.add("wrong");
    });
  }
  box.appendChild(div);
}

/* ------------------------------ progress ------------------------------ */
function renderProgress() {
  const root = $("tab-progress");
  root.replaceChildren();
  const fb = state.feedback || { answers: [], topics: [], recommendations: [] };
  const total = fb.answers.length;
  const correct = fb.answers.filter((a) => a.is_correct === true).length;
  const review = fb.answers.filter((a) => a.needs_review).length;

  const card = document.createElement("div");
  card.className = "card";
  const h = document.createElement("h2");
  h.textContent = "আমার অগ্রগতি";
  card.appendChild(h);
  const m = document.createElement("div");
  m.className = "metrics";
  [
    ["উত্তর দেওয়া", total],
    ["সঠিক", correct],
    ["শিক্ষক যাচাই করবেন", review],
  ].forEach(([label, val]) => {
    const d = document.createElement("div");
    d.className = "metric";
    const b = document.createElement("b");
    b.textContent = String(val);
    const s = document.createElement("span");
    s.textContent = label;
    d.appendChild(b);
    d.appendChild(s);
    m.appendChild(d);
  });
  card.appendChild(m);
  root.appendChild(card);

  if (!total) {
    root.appendChild(emptyCard("কুইজে অংশ নিলে এখানে আপনার অগ্রগতি দেখা যাবে।"));
    return;
  }
  if ((fb.topics || []).length) {
    const tc = document.createElement("div");
    tc.className = "card";
    const th = document.createElement("h3");
    th.textContent = "বিষয়ভিত্তিক ফল";
    tc.appendChild(th);
    fb.topics.forEach((t) => {
      const row = document.createElement("div");
      row.style.margin = ".5rem 0";
      const label = document.createElement("div");
      label.className = "row";
      const name = document.createElement("span");
      name.textContent = t.topic;
      const count = document.createElement("small");
      count.className = "muted";
      count.textContent = `${t.correct}/${t.attempts}`;
      label.appendChild(name);
      label.appendChild(count);
      const bar = document.createElement("div");
      bar.className = "bar";
      const fill = document.createElement("i");
      fill.style.width = `${Math.round((t.correct / Math.max(1, t.attempts)) * 100)}%`;
      bar.appendChild(fill);
      row.appendChild(label);
      row.appendChild(bar);
      tc.appendChild(row);
    });
    root.appendChild(tc);
  }
  (fb.recommendations || []).forEach((r) => {
    const c = emptyCard(r.suggestion_bn);
    root.appendChild(c);
  });
  const note = document.createElement("p");
  note.className = "muted";
  note.textContent = "একটি ভুল উত্তর আপনার সামগ্রিক সামর্থ্য সম্পর্কে কিছু বলে না।";
  root.appendChild(note);
}

/* ------------------------------- events ------------------------------- */
document.querySelectorAll(".tabs button").forEach((btn) => {
  btn.onclick = () => {
    document.querySelectorAll(".tabs button").forEach((b) => b.classList.remove("active"));
    btn.classList.add("active");
    ["live", "summary", "quiz", "progress"].forEach((t) => {
      $(`tab-${t}`).classList.toggle("hidden", t !== btn.dataset.tab);
    });
    if (btn.dataset.tab !== "live") userScrolled = true;
  };
});

$("joinBtn").onclick = async () => {
  const code = $("codeInput").value.trim();
  const name = $("nameInput").value.trim();
  if (code.length < 4) return ($("joinErr").textContent = "ক্লাস কোড লিখুন");
  if (!name) return ($("joinErr").textContent = "আপনার নাম লিখুন");
  $("joinBtn").disabled = true;
  try {
    await join(code, name);
    $("joinErr").textContent = "";
    connectWs();
  } catch (err) {
    $("joinErr").textContent = err.message || "যোগ দেওয়া যায়নি";
  } finally {
    $("joinBtn").disabled = false;
  }
};

$("audioBtn").onclick = () => enableAutomaticAudio();

$("leaveBtn").onclick = () => {
  localStorage.removeItem("guru.student.token");
  location.reload();
};

document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "visible") {
    fetchState();
    if (!state.ws || state.ws.readyState > 1) connectWs();
  }
});

/* Local browser-QA hooks; no playback behavior is changed by enabling them. */
if (["localhost", "127.0.0.1"].includes(location.hostname)) {
  window.__guruAudioTest = {
    handleEvent,
    setSession: (id, token = "browser-test-token") => { state.session={id}; state.token=token; },
    setTestAudio: async () => {
      window.__guruAudioTest.starts = []; window.__guruAudioTest.currentId = ""; window.__guruAudioTest.duration = 80;
      const realPlayAudio = playAudio;
      playAudio = async (seg) => { window.__guruAudioTest.currentId = seg.id; return realPlayAudio(seg); };
      state.audioContext = {
        state: "running",
        resume: async () => {},
        decodeAudioData: async () => ({ duration: window.__guruAudioTest.duration || 0.08 }),
        destination: {},
        createBufferSource: () => ({
          buffer: null, connect() {}, start() { window.__guruAudioTest.starts.push(window.__guruAudioTest.currentId); setTimeout(() => this.onended && this.onended(), window.__guruAudioTest.duration || 80); },
          onended: null,
        }),
      };
      window.fetch = async () => ({ ok: true, arrayBuffer: async () => new ArrayBuffer(4) });
      state.audioEnabled = true; state.nextPlaySeq = 1; updateAudioBtn();
    },
    snapshot: () => ({ audioEnabled: state.audioEnabled, audioStatus: state.audioStatus,
      queue: state.audioQueue.map(s => ({id:s.id,seq:s.seq})),
      played: [...state.played], nextPlaySeq: state.nextPlaySeq,
      audioBusy: state.audioBusy }),
    enable: enableAutomaticAudio,
    replay: (id) => manualReplay(state.segments.get(id)),
  };
}

/* ------------------------------- startup ------------------------------ */
(async () => {
  const code = localStorage.getItem("guru.student.code");
  if (code) $("codeInput").value = code;
  updateAudioBtn();
  const ok = await resume();
  if (!ok) {
    setConnection("off", "সংযোগ নেই");
    $("joinView").classList.remove("hidden");
  }
})();
