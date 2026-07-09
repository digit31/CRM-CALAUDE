// Rendu partagé de l'Historique (page projet + tableau de bord global).
// Chaque génération = une CARTE (active ou terminée), verte une fois finie.
(function () {
  const esc = (v) => String(v == null ? '' : v).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  function fmtHeure(iso) { if (!iso) return ''; const d = new Date(iso); if (isNaN(d)) return ''; return d.toLocaleDateString('fr-FR', { day: '2-digit', month: '2-digit', year: 'numeric' }) + ' ' + d.toLocaleTimeString('fr-FR', { hour: '2-digit', minute: '2-digit' }); }
  function fmtDuree(s) { if (s == null) return ''; s = Math.round(s); if (s < 60) return s + ' s'; const m = Math.floor(s / 60); return m + ' min ' + String(s % 60).padStart(2, '0') + ' s'; }
  function elapsed(iso) { if (!iso) return ''; const d = new Date(iso); if (isNaN(d)) return ''; return fmtDuree(Math.max(0, (Date.now() - d.getTime()) / 1000)); }

  const STAT = {
    en_cours:  { txt: 'En cours',   bd: 'border-amber-200',   tc: 'text-amber-600',   ic: 'spin' },
    en_attente:{ txt: 'En attente', bd: 'border-slate-200',   tc: 'text-slate-500',   ic: 'wait', dim: true },
    en_pause:  { txt: 'En pause',   bd: 'border-slate-300',   tc: 'text-slate-500',   ic: 'pause' },
    termine:   { txt: 'Terminé',    bd: 'border-emerald-200', tc: 'text-emerald-600', ic: 'check' },
    erreur:    { txt: 'Échec',      bd: 'border-red-200',     tc: 'text-red-600',     ic: 'x' },
    annule:    { txt: 'Annulé',     bd: 'border-slate-200',   tc: 'text-slate-400',   ic: 'ban', dim: true },
  };
  const STEP_IC = {
    en_attente: '<svg class="h-3.5 w-3.5 text-slate-300" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="9"/></svg>',
    en_cours:   '<svg class="h-3.5 w-3.5 text-amber-500 animate-spin" fill="none" viewBox="0 0 24 24"><circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle><path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.4 0 0 5.4 0 12h4z"></path></svg>',
    termine:    '<svg class="h-3.5 w-3.5 text-emerald-500" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M5 13l4 4L19 7"/></svg>',
    erreur:     '<svg class="h-3.5 w-3.5 text-red-500" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M6 18L18 6M6 6l12 12"/></svg>',
    ignore:     '<svg class="h-3.5 w-3.5 text-slate-300" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M5 12h14"/></svg>',
  };
  function bigIc(k) {
    if (k === 'check') return '<svg class="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M5 13l4 4L19 7"/></svg>';
    if (k === 'x')     return '<svg class="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M12 9v2m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"/></svg>';
    if (k === 'ban')   return '<svg class="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M18.36 5.64L5.64 18.36M12 21a9 9 0 110-18 9 9 0 010 18z"/></svg>';
    if (k === 'pause') return '<svg class="h-4 w-4" fill="currentColor" viewBox="0 0 24 24"><path d="M6 5h4v14H6zM14 5h4v14h-4z"/></svg>';
    if (k === 'wait')  return '<svg class="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z"/></svg>';
    return '<svg class="h-4 w-4 animate-spin" fill="none" viewBox="0 0 24 24"><circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle><path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.4 0 0 5.4 0 12h4z"></path></svg>';
  }
  const icBg = { spin: 'bg-amber-50 text-amber-600', wait: 'bg-slate-100 text-slate-400', pause: 'bg-slate-100 text-slate-500', check: 'bg-emerald-50 text-emerald-600', x: 'bg-red-50 text-red-600', ban: 'bg-slate-100 text-slate-500' };

  window.ctrlTache = function (pid, id, action) {
    fetch('/api/projets/' + pid + '/taches/' + id + '/' + action, { method: 'POST' })
      .then(r => r.json().then(d => ({ ok: r.ok, d })))
      .then(({ ok, d }) => { if (!ok && window.__histToast) window.__histToast(d.detail || 'Action impossible', 'error'); if (window.__histReload) window.__histReload(); })
      .catch(() => {});
  };

  function card(t, isGlobal) {
    const m = STAT[t.statut] || STAT.en_cours;
    const active = ['en_cours', 'en_attente', 'en_pause'].includes(t.statut);
    const ets = t.etapes || [];
    const multi = (t.nb_etapes || 1) > 1 || ets.length > 1;
    const heure = fmtHeure(t.fin || t.debut);
    const dureeTxt = (t.duree_s != null) ? fmtDuree(t.duree_s) : '';
    let steps = '';
    if (multi && ets.length) {
      steps = '<div class="grid grid-cols-1 sm:grid-cols-2 gap-x-4 gap-y-1.5 mt-3">' + ets.map(e => {
        const cls = e.statut === 'en_cours' ? 'text-amber-700 font-semibold' : e.statut === 'termine' ? 'text-slate-600' : e.statut === 'erreur' ? 'text-red-600' : e.statut === 'ignore' ? 'text-slate-400 line-through' : 'text-slate-400';
        const dur = (e.duree_s != null && e.statut === 'termine') ? ' · ' + fmtDuree(e.duree_s) : (e.statut === 'ignore' ? ' (déjà à jour)' : '');
        return '<div class="flex items-center gap-2 text-xs ' + cls + '">' + (STEP_IC[e.statut] || STEP_IC.en_attente) + '<span class="truncate">' + esc(e.label) + dur + '</span></div>';
      }).join('') + '</div>';
    }
    let bar = '';
    if (active) {
      const prog = Math.max(2, Math.min(100, t.progression || 0));
      const col = t.statut === 'en_pause' ? 'bg-slate-400' : (t.statut === 'en_attente' ? 'bg-slate-300' : 'bg-amber-500');
      bar = (multi || t.statut !== 'en_cours')
        ? '<div class="h-1.5 w-full bg-slate-100 rounded-full overflow-hidden mt-3"><div class="h-full ' + col + ' rounded-full transition-all duration-500" style="width:' + prog + '%"></div></div>'
        : '<div class="h-1.5 w-full bg-slate-100 rounded-full overflow-hidden mt-3"><div style="width:40%;border-radius:9999px;animation:histIndet 1.3s ease-in-out infinite" class="h-full bg-amber-500"></div></div>';
    }
    let ctrl = '';
    if (active && multi) {
      const pid = t.projet_id;
      const btn = (act, txt, cls, ic) => '<button onclick="ctrlTache(' + pid + ',\'' + t.id + '\',\'' + act + '\')" class="inline-flex items-center gap-1 px-3 py-1.5 text-xs font-semibold rounded-lg ' + cls + ' transition">' + ic + txt + '</button>';
      const pauseBtn = t.statut === 'en_pause'
        ? btn('reprendre', 'Reprendre', 'bg-emerald-600 text-white hover:bg-emerald-700', '<svg class="h-3.5 w-3.5" fill="currentColor" viewBox="0 0 24 24"><path d="M8 5v14l11-7z"/></svg>')
        : btn('pause', 'Pause', 'bg-slate-100 text-slate-600 hover:bg-slate-200', '<svg class="h-3.5 w-3.5" fill="currentColor" viewBox="0 0 24 24"><path d="M6 5h4v14H6zM14 5h4v14h-4z"/></svg>');
      ctrl = '<div class="flex gap-2 mt-3 pt-3 border-t border-slate-100">' + pauseBtn
        + btn('annuler', 'Annuler', 'bg-red-50 text-red-600 hover:bg-red-100', '<svg class="h-3.5 w-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M6 18L18 6M6 6l12 12"/></svg>')
        + '<span class="ml-auto text-[10px] text-slate-400 self-center">agit à la fin de l\'étape en cours</span></div>';
    }
    const projChip = (isGlobal && t.projet_nom) ? '<span class="inline-flex items-center px-2 py-0.5 rounded-md text-[10px] font-semibold bg-brand-50 text-brand-600 ml-2 align-middle">' + esc(t.projet_nom) + '</span>' : '';
    const sous = active
      ? (t.statut === 'en_cours' ? '<span class="' + m.tc + ' font-medium">' + esc(t.etape || m.txt) + (multi ? ' · étape ' + ((t.etape_idx || 0) + 1) + '/' + t.nb_etapes : '') + '</span>' : '<span class="' + m.tc + ' font-semibold">' + m.txt + '</span>')
      : (t.message ? '<span class="text-slate-500">' + esc(t.message) + '</span>' : '<span class="' + m.tc + '">' + m.txt + '</span>');
    const dur = dureeTxt ? '<span class="text-[11px] text-slate-400">' + dureeTxt + '</span>'
      : (active && t.debut ? '<span class="text-[11px] font-mono text-slate-400" data-debut="' + esc(t.debut) + '">' + elapsed(t.debut) + '</span>' : '');
    return '<div class="bg-white rounded-xl border ' + m.bd + ' shadow-sm p-4' + (m.dim ? ' opacity-70' : '') + '">'
      + '<div class="flex items-start gap-3">'
      + '<div class="mt-0.5 flex-shrink-0 h-8 w-8 rounded-lg flex items-center justify-center ' + (icBg[m.ic] || icBg.spin) + '">' + bigIc(m.ic) + '</div>'
      + '<div class="flex-1 min-w-0">'
      + '<div class="flex items-start justify-between gap-3">'
      + '<p class="font-semibold text-slate-800 text-sm">' + esc(t.label) + projChip + '</p>'
      + '<div class="text-right flex-shrink-0">' + (heure ? '<p class="text-[11px] text-slate-400">' + heure + '</p>' : '') + dur + '</div>'
      + '</div>'
      + '<p class="text-xs mt-0.5">' + sous + '</p>'
      + steps + bar + ctrl
      + '</div></div></div>';
  }

  // opts: { endpoint, global, actives, secActives, terminees, vide, toast }
  window.histInit = function (opts) {
    const O = opts || {};
    const isGlobal = !!O.global;
    const $act = document.getElementById(O.actives);
    const $secAct = O.secActives ? document.getElementById(O.secActives) : null;
    const $term = document.getElementById(O.terminees);
    const $vide = O.vide ? document.getElementById(O.vide) : null;
    let prev = 0;
    window.__histToast = O.toast || function () {};
    function charger() {
      fetch(O.endpoint + (O.endpoint.indexOf('?') >= 0 ? '&' : '?') + '_t=' + Date.now())
        .then(r => r.json())
        .then(d => {
          const act = d.actives || [];
          if ($secAct) $secAct.classList.toggle('hidden', act.length === 0);
          if ($act) $act.innerHTML = act.map(t => card(t, isGlobal)).join('');
          const term = d.terminees || [];
          if ($vide) $vide.classList.toggle('hidden', term.length > 0);
          if ($term) $term.innerHTML = term.map(t => card(t, isGlobal)).join('');
          if (prev > 0 && act.length === 0) window.__histToast('Génération terminée.', 'ok');
          prev = act.length;
        }).catch(() => {});
    }
    window.__histReload = charger;
    setInterval(() => { document.querySelectorAll('[data-debut]').forEach(s => { s.textContent = elapsed(s.dataset.debut); }); }, 1000);
    charger();
    setInterval(charger, 1500);
  };

  // keyframes pour la barre indéterminée
  const st = document.createElement('style');
  st.textContent = '@keyframes histIndet{0%{margin-left:-42%}100%{margin-left:100%}}';
  document.head.appendChild(st);
})();
