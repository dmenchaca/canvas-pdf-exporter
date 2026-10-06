# Canvas PDF Exporter

Een Chrome-extensie om Canvas-pagina's automatisch te exporteren naar PDF.

## Installatie-instructies

### Stap 1: Download de code

1. Ga naar https://github.com/dmenchaca/canvas-pdf-exporter
2. Klik op de groene knop **"Code"**
3. Selecteer **"Download ZIP"**
4. Pak het ZIP-bestand uit op je computer

### Stap 2: Installeer de extensie in Chrome

1. Open Chrome en ga naar `chrome://extensions/`
2. Schakel **"Ontwikkelaarsmodus"** in (toggle rechtsboven)
3. Klik op **"Uitgepakte extensie laden"**
4. Selecteer de uitgepakte map `canvas-pdf-exporter`
5. De extensie is nu geïnstalleerd en verschijnt in je werkbalk

### Stap 3: Gebruik de extensie

1. Navigeer naar je Canvas-cursus (pagina 1)
2. Klik op het extensie-icoon in je werkbalk
3. Configureer de instellingen (optioneel, klik op "Geavanceerde instellingen"):
   - **Totaal aantal pagina's**: Aantal pagina's om vast te leggen
   - **Vertraging**: Tijd tussen pagina-overgangen (200ms standaard)
   - **Kwaliteit**: JPEG-kwaliteit of PNG voor verliesvrij (Hoog standaard)
   - **Resolutie**: Normaal (1x) of Hoog (2x) voor scherpere afbeeldingen
4. Klik op **"Download als PDF"**

## Twee soorten cursussen

De extensie herkent automatisch welk type cursus open staat:

- **Klassiek** (mode A): de oude SCORM-speler met losse dia's. Elke dia wordt als afbeelding vastgelegd en samengevoegd tot een PDF. Dit werkt precies zoals in versie 1.9.
- **Rise** (mode B): de nieuwe Canvas-speler met een rood hoofdstukkenmenu (Articulate Rise). Alle hoofdstukken worden na elkaar geopend en met de printfunctie van Chrome omgezet naar een PDF met **echte, selecteerbare tekst**. Tijdens het exporteren toont Chrome een gele balk "Canvas PDF Exporter is begonnen met foutopsporing"; dat is normaal en verdwijnt na afloop.

- **Storyline** (mode C): e-modules gemaakt met Articulate Storyline (losse dia's, vaak met een keuzemenu). De extensie leest de cursusdata (alle dia's, vragen met juiste antwoorden, afbeeldingen) en downloadt die als `<cursus>.sl.json`. De PDF maak je daarna lokaal:

  ```bash
  python3 storyline-pdf.py "<cursus>.sl.json"
  ```

  Dit levert dezelfde stijl als mode B (echte tekst, hoofdstukken met inhoudsopgave, vraagkaders met het juiste antwoord aangevinkt en een toelichting, echte PDF-vinkvakjes). Vereist Python 3 met `reportlab` en `Pillow` (`pip3 install reportlab pillow`). Zonder extensie: plak `console-storyline.js` in de console van het spelertabblad.

Tips voor Rise-cursussen:
- Sluit DevTools (F12) voordat je exporteert, anders kan de extensie niet aan het tabblad koppelen.
- Interactieve blokken (flashcards, sorteeroefeningen, scenario's) worden afgedrukt zoals ze op dat moment zichtbaar zijn; video's als stilstaand beeld.
- Met **Schaal** in de geavanceerde instellingen past meer inhoud op een pagina.

## Functies

- ✅ Stop-knop om opname halverwege te stoppen
- ✅ Hoogwaardige opname met 2x resolutie optie
- ✅ Kwaliteitsinstellingen van Laag tot Maximum (PNG)
- ✅ Voortgangsbalk
- ✅ Automatische PDF-generatie
- ✅ Automatische detectie van totaal aantal pagina's
- ✅ Rise-cursussen: PDF met echte tekst van alle hoofdstukken
- ✅ Paginanummers aan: ook de naam van de e-module links in de voettekst
- ✅ Rise: canvasblokken en sorteeroefeningen (met de juiste indeling) worden meegenomen
- ✅ Storyline-cursussen: PDF met echte tekst, afbeeldingen en beantwoorde vragen

## Privacy

Canvas PDF Exporter verzamelt geen gebruikersgegevens en werkt volledig lokaal op je apparaat.

- 📄 [Privacybeleid (Nederlands)](PRIVACY_NL.md)
- 📄 [Privacy Policy (English)](PRIVACY.md)

## Ondersteuning

Voor vragen of problemen, maak een issue aan op GitHub: https://github.com/dmenchaca/canvas-pdf-exporter/issues
