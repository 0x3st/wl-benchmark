"""Embedded, dependency-free presentation for the single-file run report."""

CSS = """
:root { color-scheme: light; --bg:#f4f6f8; --paper:#fff; --ink:#182a3a;
  --muted:#5f6f7f; --line:#dfe6ec; --accent:#116b60; --soft:#eaf5f1;
  --red:#ac3541; --amber:#805918; --mono:ui-monospace,SFMono-Regular,Consolas,monospace; }
* { box-sizing:border-box; }
html { scroll-padding-top:24px; }
body { margin:0; background:var(--bg); color:var(--ink); font:14px/1.6
  -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC",sans-serif; overflow-wrap:anywhere; }
a { color:var(--accent); text-decoration:none; }
a:hover { text-decoration:underline; }
button,input,select { font:inherit; }
button,select,summary { cursor:pointer; }
button,a,input,select,summary { -webkit-tap-highlight-color:transparent; }
:focus-visible { outline:3px solid #298bdb; outline-offset:4px; }
[hidden] { display:none !important; }
.js-only { display:none; }
.js .js-only { display:initial; }
.skip-link { position:absolute; top:-100px; padding:12px; background:white; z-index:5; }
.skip-link:focus { top:8px; }
.topbar { background:var(--paper); border-bottom:1px solid var(--line); }
.topbar-inner { max-width:1440px; margin:auto; padding:18px 36px; display:flex;
  align-items:center; justify-content:space-between; gap:20px; }
.brand { display:flex; align-items:center; gap:10px; color:var(--ink); font-weight:750;
  letter-spacing:.04em; font-size:13px; }
.brand-mark { display:grid; place-items:center; width:34px; height:34px;
  background:var(--ink); color:white; border-radius:9px; font-size:12px; }
.top-links { display:flex; align-items:center; gap:22px; font-size:12px; }
.shell { max-width:1440px; margin:auto; padding:36px; }
.hero { display:flex; align-items:flex-start; justify-content:space-between; gap:24px; margin-bottom:26px; }
.eyebrow { color:var(--muted); text-transform:uppercase; font-size:10px;
  font-weight:750; letter-spacing:.13em; margin:0 0 10px; }
h1 { font-size:clamp(24px,3vw,36px); line-height:1.25; letter-spacing:-.035em; margin:0 0 12px; }
h2 { font-size:20px; line-height:1.35; letter-spacing:-.025em; margin:0; }
h3 { font-size:14px; margin:22px 0 10px; }
p { margin:10px 0; }
.meta { color:var(--muted); font-size:12px; }
.hero .meta { margin:4px 0; }
.hero-note { max-width:270px; flex-shrink:0; border-left:2px solid var(--accent);
  padding-left:16px; margin-top:6px; font-size:12px; color:var(--muted); }
.hero-note strong { display:block; color:var(--ink); margin-bottom:5px; }
.stats { display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:14px; margin:0 0 10px; }
.stat { border:1px solid var(--line); border-radius:12px; background:white; padding:18px 22px; }
.stat-label { color:var(--muted); font-size:11px; font-weight:650; }
.stat-value { display:block; font-size:30px; line-height:1.4; font-weight:650; font-variant-numeric:tabular-nums; }
.stat-note { font-size:11px; color:var(--muted); }
.stat.warn .stat-value { color:var(--amber); }
.stat.danger .stat-value { color:var(--red); }
.score-note { margin:0 0 28px; font-size:11px; color:var(--muted); }
.workspace { display:grid; grid-template-columns:218px minmax(0,1fr); align-items:start; gap:28px; }
.sidebar { position:sticky; top:24px; max-height:calc(100vh - 48px); overflow:auto; padding-right:6px; }
.nav-heading { display:flex; justify-content:space-between; color:var(--muted); text-transform:uppercase;
  font-size:10px; font-weight:700; letter-spacing:.1em; margin:20px 8px 8px; }
.nav-heading:first-of-type { margin-top:0; }
.nav-link { display:flex; align-items:flex-start; gap:9px; padding:10px; margin:3px 0; border-radius:8px;
  color:var(--muted); line-height:1.4; font-size:12px; }
.nav-link:hover { background:#e9eef2; text-decoration:none; color:var(--ink); }
.nav-link[aria-current] { background:var(--soft); color:var(--accent); }
.nav-number { flex-shrink:0; font:10px/1.8 var(--mono); color:var(--muted); }
.nav-link small { display:block; margin-top:3px; font-size:10px; color:var(--muted); }
.main-column { min-width:0; }
.panel { border:1px solid var(--line); background:white; border-radius:12px; }
.completion-log { margin-bottom:18px; padding:14px 18px; font-size:12px; }
summary { font-weight:650; }
summary .meta { font-weight:400; margin-left:8px; }
.toolbar { margin:0 0 20px; }
.js .toolbar { display:block; }
.tabs { display:flex; flex-wrap:wrap; gap:5px; border-bottom:1px solid var(--line); padding-bottom:12px; }
.tab { border:0; background:transparent; color:var(--muted); padding:7px 12px; border-radius:7px; font-size:12px; }
.tab[aria-pressed=true] { color:white; background:var(--ink); }
.tab .count { opacity:.7; margin-left:6px; font-size:10px; }
.filter-row { display:flex; flex-wrap:wrap; align-items:end; gap:10px; margin-top:12px; }
.field { display:flex; flex-direction:column; gap:4px; color:var(--muted); font-size:10px; font-weight:600; }
.field.search { flex:1 1 180px; }
.field input,.field select { width:100%; min-width:0; border:1px solid var(--line); border-radius:7px;
  padding:8px 10px; background:white; color:var(--ink); font-size:12px; }
.results-count { margin:10px 0 0; font-size:11px; color:var(--muted); }
.task-card { background:white; border:1px solid var(--line); border-radius:14px; margin-bottom:24px;
  box-shadow:0 2px 4px #182a3a03; scroll-margin-top:24px; }
.task-header { padding:22px 26px 18px; border-bottom:1px solid var(--line); }
.task-topline { display:flex; justify-content:space-between; align-items:center; gap:12px; margin-bottom:12px; }
.task-type { color:var(--accent); text-transform:uppercase; font-size:10px; font-weight:750; letter-spacing:.1em; }
.task-topline .meta { font:10px/1.5 var(--mono); }
.task-title-row { display:flex; justify-content:space-between; align-items:flex-start; gap:16px; }
.task-id { display:block; font:11px/1.5 var(--mono); color:var(--muted); margin-top:6px; }
.task-content { padding:22px 26px 26px; }
.badge { display:inline-block; border-radius:5px; padding:4px 8px; font-size:10px; font-weight:650; }
.badge.ok { background:var(--soft); color:var(--accent); }
.badge.pen { background:#fff4dc; color:var(--amber); }
.badge.err { background:#fcecef; color:var(--red); }
.badge-wrap { text-align:right; flex-shrink:0; }
.badge-wrap .score { display:block; margin-top:5px; font:600 16px/1.4 var(--mono); }
.callout { background:#f8fafb; border:1px solid var(--line); border-radius:8px; padding:14px 16px; font-size:12px; }
.callout.warning { background:#fffaf0; border-color:#efddae; color:var(--amber); }
.callout.error { background:#fff5f6; border-color:#f0cdd2; color:var(--red); }
.empty-state { text-align:center; padding:38px 20px; border:1px dashed #cbd6de; border-radius:10px;
  background:#f9fbfc; color:var(--muted); font-size:12px; }
.empty-state strong { display:block; color:var(--ink); font-size:15px; margin-bottom:8px; }
.empty-state p { margin:0 auto; max-width:58ch; }
.checks { border-top:1px solid var(--line); margin-top:20px; padding-top:16px; font-size:12px; }
.checks summary { display:list-item; }
.checks .ok-text,.ok-text { color:var(--accent); font-weight:650; white-space:nowrap; }
.fail-text { color:var(--red); font-weight:650; white-space:nowrap; }
.table-scroll { max-width:100%; overflow-x:auto; margin:12px 0; }
table { border-collapse:collapse; width:100%; font-size:12px; }
th { color:var(--muted); text-align:left; font-size:10px; font-weight:650; background:#f7f9fb; }
th,td { border-bottom:1px solid var(--line); padding:10px 12px; vertical-align:top; overflow-wrap:anywhere; }
.table-scroll th { white-space:nowrap; }
tr:last-child td { border-bottom:0; }
.num { font-variant-numeric:tabular-nums; font-family:var(--mono); font-size:11px; }
.failed-row { background:#fff8f8; }
.picks { display:flex; flex-wrap:wrap; gap:8px; margin:0 0 16px; }
.pick { padding:5px 10px; background:#f2f6f8; border-radius:5px; font-size:11px; }
.pick span { color:var(--muted); margin-right:6px; }
figure { margin:0; }
.artwork { border:1px solid var(--line); border-radius:10px; padding:16px; background:#f8fafb; text-align:center; }
.artwork img { display:block; margin:auto; max-width:100%; max-height:620px; width:auto; height:auto; background:white; }
figcaption { font-size:11px; color:var(--muted); margin-top:12px; }
.artifact-actions { display:flex; flex-wrap:wrap; align-items:center; gap:10px; margin-top:14px; }
.button { display:inline-block; border:1px solid #ccd8de; background:white; color:var(--ink);
  border-radius:7px; padding:7px 12px; font-size:11px; font-weight:600; }
.button:hover { background:#f1f6f7; text-decoration:none; }
.button.primary { background:var(--accent); color:white; border-color:var(--accent); }
.button[aria-pressed=true] { background:var(--soft); color:var(--accent); border-color:var(--accent); }
.reading { margin-top:18px; }
.reading > summary { border-bottom:1px solid var(--line); padding-bottom:12px; font-size:13px; }
.prose { max-width:76ch; margin:22px auto 0; font-size:15px; line-height:1.9; color:#2b3b48; overflow-wrap:anywhere; }
.prose h2 { margin:30px 0 14px; font-size:21px; }
.prose h3,.prose h4,.prose h5 { margin:24px 0 10px; line-height:1.5; }
.prose p { margin:14px 0; }
.prose li { margin:6px 0; }
pre { white-space:pre-wrap; overflow-wrap:anywhere; max-width:100%; background:#f3f6f8; padding:16px;
  border-radius:8px; font:12px/1.7 var(--mono); }
code { font-family:var(--mono); font-size:.88em; background:#eef3f5; padding:2px 4px; border-radius:3px; }
pre code { font-size:inherit; background:none; padding:0; }
blockquote { margin:16px 0; padding:8px 18px; border-left:3px solid var(--accent); background:var(--soft); }
.quant-summary { display:flex; flex-wrap:wrap; align-items:center; justify-content:space-between; gap:14px;
  padding:16px 18px; background:var(--soft); border-radius:9px; }
.quant-summary strong { font-size:24px; font-weight:650; }
.quant-summary .meta { max-width:50ch; }
.sampling { margin-top:12px; }
.footer { color:var(--muted); font-size:11px; border-top:1px solid var(--line); padding:22px 0; margin-top:32px; }
dialog { width:min(94vw,1200px); max-height:94vh; padding:20px; border:1px solid var(--line); border-radius:12px; }
dialog::backdrop { background:#142434bb; }
.dialog-head { display:flex; justify-content:space-between; gap:16px; align-items:center; margin-bottom:16px; }
.dialog-head h2 { font-size:16px; }
.zoom-canvas { overflow:auto; max-height:75vh; background:#f5f7f8; }
.zoom-canvas img { display:block; max-width:100%; height:auto; margin:auto; }
.zoom-canvas.actual img { max-width:none; }
.zoom-hint { font-size:11px; color:var(--muted); }
@media (max-width:1000px) {
 .shell { padding:24px; } .topbar-inner { padding:14px 24px; }
 .workspace { grid-template-columns:178px minmax(0,1fr); gap:20px; }
 .hero-note { display:none; } .task-header,.task-content { padding:20px; }
 .stat { padding:14px 16px; }
}
@media (max-width:720px) {
 .shell { padding:22px 14px; } .topbar-inner { padding:12px 14px; }
 .top-links { gap:12px; } .top-links .docs-link { display:none; }
 .workspace { display:block; } .sidebar { position:static; max-height:none; padding:0; }
 .sidebar details { background:white; border:1px solid var(--line); border-radius:10px; padding:12px; margin-bottom:16px; }
 .sidebar nav { max-height:240px; overflow:auto; }
 .stats { grid-template-columns:repeat(2,minmax(0,1fr)); gap:8px; }
 .stat { padding:12px 14px; } .stat-value { font-size:26px; } .hero { margin-bottom:20px; }
 .stat-note { display:block; line-height:1.5; }
 .task-header,.task-content { padding:16px; }
 .task-title-row { flex-wrap:wrap; gap:10px; } .badge-wrap { text-align:left; }
 .badge-wrap .score { display:inline; margin-left:6px; }
 .task-topline { flex-wrap:wrap; gap:4px; } .artwork { padding:5px; }
 .field { flex:1 1 120px; } .prose { font-size:14px; }
 .tab { padding:7px 9px; } h2 { font-size:18px; }
}
@media print {
 body { background:white; font-size:11px; }
 .topbar,.sidebar,.toolbar,.completion-log,.artifact-actions,.js-only,dialog { display:none !important; }
 .shell { max-width:none; padding:0; } .workspace { display:block; }
 .stats { grid-template-columns:repeat(4,1fr); }
 .task-card { box-shadow:none; border-radius:0; break-before:page; }
 .task-header { break-after:avoid; } .prose { max-width:none; font-size:12px; }
 .table-scroll { overflow:visible; } pre { white-space:pre-wrap; } a { color:inherit; }
}
"""

SCRIPT = r"""
(() => {
  'use strict';
  const cards = [...document.querySelectorAll('.task-card')];
  const list = document.getElementById('task-list');
  const search = document.getElementById('task-search');
  const status = document.getElementById('status-filter');
  const order = document.getElementById('task-order');
  const tabs = [...document.querySelectorAll('[data-filter]')];
  let type = 'all';
  function applyFilters() {
    const query = search.value.trim().toLowerCase();
    let visible = 0;
    cards.forEach(card => {
      const matchesStatus = status.value === 'all' || card.dataset.status === status.value ||
        (status.value === 'preview' && card.dataset.preview === 'missing');
      card.hidden = !(matchesStatus && (type === 'all' || card.dataset.kind === type) &&
        card.dataset.search.includes(query));
      if (!card.hidden) visible++;
    });
    document.getElementById('results-count').textContent = `${visible} of ${cards.length} tasks shown`;
    document.getElementById('no-matches').hidden = visible !== 0 || cards.length === 0;
  }
  function setType(next) {
    type = next;
    tabs.forEach(tab => tab.setAttribute('aria-pressed', String(tab.dataset.filter === type)));
  }
  tabs.forEach(tab => tab.addEventListener('click', () => {
    setType(tab.dataset.filter); applyFilters();
  }));
  search.addEventListener('input', applyFilters);
  status.addEventListener('change', applyFilters);
  order.addEventListener('change', () => {
    [...cards].sort((a, b) => (order.value === 'group'
      ? Number(a.dataset.group) - Number(b.dataset.group) : 0) ||
      Number(a.dataset.order) - Number(b.dataset.order)).forEach(card => list.append(card));
  });
  function resetFilters() {
    setType('all'); search.value = ''; status.value = 'all'; applyFilters();
  }
  document.getElementById('reset-filters').addEventListener('click', resetFilters);
  function revealHash() {
    const target = document.getElementById(location.hash.slice(1));
    if (!target || !target.classList.contains('task-card')) return;
    if (target.hidden) resetFilters();
    document.querySelectorAll('.nav-link').forEach(link => {
      if (link.hash === location.hash) link.setAttribute('aria-current', 'location');
      else link.removeAttribute('aria-current');
    });
    target.scrollIntoView({block:'start'});
  }
  // Anchor navigation must also work when the target is currently filtered out.
  document.querySelectorAll('a[href^="#task-"]').forEach(link => {
    link.addEventListener('click', resetFilters);
  });
  window.addEventListener('hashchange', revealHash);
  const dialog = document.getElementById('image-dialog');
  if (typeof dialog.showModal === 'function') {
    document.querySelectorAll('[data-preview-target]').forEach(button => {
      button.hidden = false;
      button.addEventListener('click', () => {
        const source = document.getElementById(button.dataset.previewTarget);
        document.getElementById('zoom-image').src = source.src;
        document.getElementById('zoom-image').alt = source.alt;
        document.getElementById('zoom-title').textContent = source.alt;
        document.getElementById('zoom-canvas').classList.remove('actual');
        document.getElementById('zoom-toggle').setAttribute('aria-pressed', 'false');
        dialog.showModal();
      });
    });
    document.getElementById('zoom-close').addEventListener('click', () => dialog.close());
    document.getElementById('zoom-toggle').addEventListener('click', event => {
      const actual = document.getElementById('zoom-canvas').classList.toggle('actual');
      event.currentTarget.setAttribute('aria-pressed', String(actual));
    });
  }
  // Print the complete report, not only the currently filtered/collapsed view.
  let printState;
  window.addEventListener('beforeprint', () => {
    printState = {hidden: cards.map(card => card.hidden), details:
      [...document.querySelectorAll('details')].map(el => [el, el.open])};
    cards.forEach(card => { card.hidden = false; });
    printState.details.forEach(([el]) => { el.open = true; });
  });
  window.addEventListener('afterprint', () => {
    if (!printState) return;
    cards.forEach((card, i) => { card.hidden = printState.hidden[i]; });
    printState.details.forEach(([el, open]) => { el.open = open; });
    printState = null;
  });
  document.getElementById('print-report').addEventListener('click', () => window.print());
  const directory = document.getElementById('directory');
  directory.open = !window.matchMedia('(max-width:720px)').matches;
  document.documentElement.classList.add('js');
  applyFilters(); revealHash();
})();
"""
