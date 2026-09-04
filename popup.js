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
  if (currentMode() === 'rise') {
    runRiseMode();
  } else {
    runClassicMode();
  }
});

document.getElementById('stopBtn').addEventListener('click', () => {
  if (currentMode() === 'rise') {
    stopRiseMode();
  } else {
    stopClassicMode();
  }
});

// Accordion functionality
document.addEventListener('DOMContentLoaded', () => {
  const accordion = document.querySelector('.accordion');
  if (accordion) {
    accordion.addEventListener('click', function () {
      this.classList.toggle('active');
      const panel = document.getElementById('advancedPanel');
      panel.classList.toggle('show');
    });
  }

  applyModeUI();

  // Automatische detectie van het type speler op het actieve tabblad.
  chrome.tabs.query({ active: true, currentWindow: true }, async (tabs) => {
    if (!tabs[0]) return;
    const tabId = tabs[0].id;

    const rise = await detectRiseCourse(tabId);
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
    if (pages) document.getElementById('totalPages').value = pages;
  });
});
