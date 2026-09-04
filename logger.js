// Logboek voor de popup. Alles wat de extensie doet wordt met tijdstip vastgelegd,
// bewaard in chrome.storage.local (overleeft het sluiten van de popup) en kan met
// één klik gekopieerd of gedownload worden om te delen bij problemen.

const Log = (() => {
  const MAX_LINES = 3000;
  let lines = [];
  let box = null;

  function stamp() {
    const d = new Date();
    const p = (n, w = 2) => String(n).padStart(w, '0');
    return `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}.${p(d.getMilliseconds(), 3)}`;
  }

  function fmt(data) {
    if (data === undefined) return '';
    try {
      if (data instanceof Error) return ' ' + (data.stack || data.message || String(data));
      return ' ' + JSON.stringify(data);
    } catch (e) {
      return ' ' + String(data);
    }
  }

  function persist() {
    try { chrome.storage.local.set({ cpeLog: lines }); } catch (e) { /* geen storage */ }
  }

  function render() {
    if (!box) box = document.getElementById('logBox');
    if (box) {
      box.value = lines.join('\n');
      box.scrollTop = box.scrollHeight;
    }
  }

  function add(level, msg, data) {
    const line = `[${stamp()}] ${level.padEnd(5)} ${msg}${fmt(data)}`;
    lines.push(line);
    if (lines.length > MAX_LINES) lines = lines.slice(lines.length - MAX_LINES);
    (level === 'ERROR' ? console.error : console.log)(line);
    render();
    persist();
  }

  const api = {
    info: (m, d) => add('INFO', m, d),
    warn: (m, d) => add('WARN', m, d),
    error: (m, d) => add('ERROR', m, d),
    debug: (m, d) => add('DEBUG', m, d),
    text: () => lines.join('\n'),
    clear() { lines = []; persist(); render(); api.header(); },
    async restore() {
      try {
        const r = await chrome.storage.local.get('cpeLog');
        if (Array.isArray(r.cpeLog)) { lines = r.cpeLog; render(); }
      } catch (e) { /* geen storage */ }
    },
    header() {
      const m = chrome.runtime.getManifest();
      add('INFO', `---- ${m.name} v${m.version} popup geopend ----`);
      add('INFO', 'Browser', navigator.userAgent);
      add('INFO', 'Datum', new Date().toISOString());
    },
    async copy() {
      try {
        await navigator.clipboard.writeText(api.text());
        return true;
      } catch (e) {
        if (!box) box = document.getElementById('logBox');
        if (box) { box.select(); document.execCommand('copy'); return true; }
        return false;
      }
    },
    download() {
      const blob = new Blob([api.text()], { type: 'text/plain' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `canvas-pdf-exporter-log-${new Date().toISOString().replace(/[:.]/g, '-')}.txt`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 5000);
    }
  };

  window.addEventListener('error', (e) => add('ERROR', 'Onverwachte fout', e.error || e.message));
  window.addEventListener('unhandledrejection', (e) => add('ERROR', 'Onafgehandelde promise', e.reason));

  return api;
})();
