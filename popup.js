// Aansturing van de popup: kiest tussen mode A (klassieke speler, schermafbeeldingen)
// en mode B (Rise-cursus, echte tekst) en schakelt de bijbehorende instellingen.

function currentMode() {
  return document.getElementById('mode').value;
}

function applyModeUI() {
  const mode = currentMode();
  document.querySelectorAll('.classic-only').forEach((el) => { el.style.display = mode === 'classic' ? '' : 'none'; });
  document.querySelectorAll('.rise-only').forEach((el) => { el.style.display = mode === 'rise' ? '' : 'none'; });
  document.getElementById('hint').textContent = mode === 'rise'
    ? 'Maakt een PDF met echte tekst van alle hoofdstukken (Rise-cursus).'
    : 'Maakt automatisch een PDF van de cursusinhoud.';
}

document.getElementById('mode').addEventListener('change', applyModeUI);

document.getElementById('startBtn').addEventListener('click', () => {
  Log.info('Start geklikt', { mode: currentMode() });
  if (currentMode() === 'rise') {
    runRiseMode();
  } else {
    runClassicMode();
  }
});

document.getElementById('stopBtn').addEventListener('click', () => {
  Log.info('Stop geklikt');
  if (currentMode() === 'rise') {
    stopRiseMode();
  } else {
    stopClassicMode();
  }
});

// Accordion functionality
document.addEventListener('DOMContentLoaded', async () => {
  const bind = (btnSel, panelId) => {
    const btn = document.querySelector(btnSel);
    if (!btn) return;
    btn.addEventListener('click', function () {
      this.classList.toggle('active');
      document.getElementById(panelId).classList.toggle('show');
    });
  };
  bind('.accordion:not(#logAccordion)', 'advancedPanel');
  bind('#logAccordion', 'logPanel');

  document.getElementById('logCopy').addEventListener('click', async () => {
    const ok = await Log.copy();
    document.getElementById('status').textContent = ok ? 'Logboek gekopieerd naar klembord.' : 'Kopiëren mislukt; gebruik Download.';
  });
  document.getElementById('logDownload').addEventListener('click', () => Log.download());
  document.getElementById('logClear').addEventListener('click', () => Log.clear());

  await Log.restore();
  Log.header();
  document.getElementById('mode').addEventListener('change', () => Log.info('Modus handmatig gewijzigd', currentMode()));

  applyModeUI();

  // Automatische detectie van het type speler op het actieve tabblad.
  chrome.tabs.query({ active: true, currentWindow: true }, async (tabs) => {
    if (!tabs[0]) { Log.warn('Geen actief tabblad gevonden'); return; }
    const tabId = tabs[0].id;
    Log.info('Actief tabblad', { id: tabId, url: tabs[0].url, title: tabs[0].title });

    await logFrameDiagnostics(tabId);

    const rise = await detectRiseCourse(tabId);
    Log.info('Rise-detectie resultaat', rise);
    if (rise && rise.lessons.length) {
      document.getElementById('mode').value = 'rise';
      document.getElementById('riseInfo').textContent =
        `${rise.courseTitle} · ${rise.lessons.length} hoofdstuk${rise.lessons.length === 1 ? '' : 'ken'} gevonden`;
      applyModeUI();
      return;
    }

    document.getElementById('mode').value = 'classic';
    applyModeUI();
    const pages = await detectClassicTotalPages(tabId);
    Log.info('Klassieke detectie: totaal pagina\'s', pages);
    if (pages) document.getElementById('totalPages').value = pages;
  });
});
