let stopRequested = false;

document.getElementById('startBtn').addEventListener('click', async () => {
  const totalPages = parseInt(document.getElementById('totalPages').value);
  const delay = parseInt(document.getElementById('delay').value);
  const qualityValue = document.getElementById('quality').value;
  const cropToDiv = true; // Always crop to content div
  const status = document.getElementById('status');
  const startBtn = document.getElementById('startBtn');
  const stopBtn = document.getElementById('stopBtn');
  const progressContainer = document.getElementById('progressContainer');
  const progressBar = document.getElementById('progressBar');
  
  const usePng = qualityValue === 'png';
  const quality = usePng ? 1 : parseFloat(qualityValue);
  
  stopRequested = false;
  startBtn.disabled = true;
  startBtn.style.display = 'none';
  stopBtn.style.display = 'block';
  progressContainer.style.display = 'block';
  
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  
  // Hide navbar by setting opacity to 0
  await chrome.scripting.executeScript({
    target: { tabId: tab.id },
    func: () => {
      const iframe = document.getElementById('ScormContent');
      if (iframe && iframe.contentDocument) {
        const pagenav = iframe.contentDocument.querySelector('.pagenav');
        if (pagenav) {
          pagenav.style.opacity = '0';
        }
      }
    }
  });
  
  await new Promise(r => setTimeout(r, 200)); // Wait for opacity change
  
  const captures = [];
  
  for (let page = 1; page <= totalPages; page++) {
    if (stopRequested) break;
    
    status.textContent = `Pagina ${page} van ${totalPages} vastleggen...`;
    progressBar.style.width = `${(page / totalPages) * 100}%`;
    
    await new Promise(r => setTimeout(r, 300));
    
    // Capture full viewport
    const dataUrl = await chrome.tabs.captureVisibleTab(null, { format: 'png' });
    
    // Get outerClipDiv position if cropping enabled
    let croppedDataUrl = dataUrl;
    if (cropToDiv) {
      const [cropInfo] = await chrome.scripting.executeScript({
        target: { tabId: tab.id },
        func: () => {
          // Find outerClipDiv inside the iframe
          const iframe = document.getElementById('ScormContent');
          if (iframe && iframe.contentDocument) {
            const outerDiv = iframe.contentDocument.getElementById('outerClipDiv');
            if (outerDiv) {
              const rect = outerDiv.getBoundingClientRect();
              const iframeRect = iframe.getBoundingClientRect();
              
              return {
                x: iframeRect.left + rect.left,
                y: iframeRect.top + rect.top,
                width: rect.width,
                height: rect.height,
                devicePixelRatio: window.devicePixelRatio
              };
            }
          }
          return null;
        }
      });
      
      if (cropInfo.result) {
        croppedDataUrl = await cropImage(dataUrl, cropInfo.result, usePng, quality);
      }
    }
    
    captures.push(croppedDataUrl);
    
    if (page < totalPages) {
      // Click next button
      await chrome.scripting.executeScript({
        target: { tabId: tab.id },
        func: () => {
          const iframe = document.getElementById('ScormContent');
          if (iframe && iframe.contentDocument) {
            const doc = iframe.contentDocument;
            const buttons = doc.querySelectorAll('a.navbutton');
            for (let btn of buttons) {
              if (btn.querySelector('.fa-caret-right')) {
                btn.click();
                return true;
              }
            }
          }
          return false;
        }
      });
      
      await new Promise(r => setTimeout(r, delay));
    }
  }
  
  // Restore navbar opacity
  await chrome.scripting.executeScript({
    target: { tabId: tab.id },
    func: () => {
      const iframe = document.getElementById('ScormContent');
      if (iframe && iframe.contentDocument) {
        const pagenav = iframe.contentDocument.querySelector('.pagenav');
        if (pagenav) {
          pagenav.style.opacity = '1';
        }
      }
    }
  });
  
  status.textContent = 'PDF maken...';
  
  // Get page title for PDF filename
  const [titleResult] = await chrome.scripting.executeScript({
    target: { tabId: tab.id },
    func: () => {
      // Try to get title from meta tag or document title
      const metaTitle = document.querySelector('meta[property="og:title"]');
      if (metaTitle) return metaTitle.content;
      
      const iframe = document.getElementById('ScormContent');
      if (iframe && iframe.contentDocument) {
        const iframeTitle = iframe.contentDocument.title;
        if (iframeTitle) return iframeTitle;
      }
      
      return document.title || 'scorm-export';
    }
  });
  
  let pdfFilename = 'scorm-export.pdf';
  if (titleResult && titleResult.result) {
    // Sanitize filename - remove invalid characters
    const sanitized = titleResult.result
      .replace(/[<>:"/\\|?*]/g, '') // Remove invalid chars
      .replace(/\s+/g, '-')          // Replace spaces with dashes
      .substring(0, 100);            // Limit length
    pdfFilename = sanitized + '.pdf';
  }
  
  const { jsPDF } = window.jspdf;
  
  const firstImg = await loadImage(captures[0]);
  const imgWidth = firstImg.width;
  const imgHeight = firstImg.height;
  
  const pdf = new jsPDF({
    orientation: imgWidth > imgHeight ? 'landscape' : 'portrait',
    unit: 'px',
    format: [imgWidth, imgHeight],
    compress: true
  });
  
  const imageFormat = usePng ? 'PNG' : 'JPEG';
  
  for (let i = 0; i < captures.length; i++) {
    status.textContent = `Pagina ${i + 1} van ${captures.length} toevoegen aan PDF...`;
    
    if (i > 0) {
      pdf.addPage([imgWidth, imgHeight], imgWidth > imgHeight ? 'landscape' : 'portrait');
    }
    
    pdf.addImage(captures[i], imageFormat, 0, 0, imgWidth, imgHeight, undefined, 'FAST');
  }
  
  pdf.save(pdfFilename);
  
  status.textContent = `Klaar! PDF met ${captures.length} pagina's gedownload.`;
  resetButtons();
  
  function resetButtons() {
    startBtn.disabled = false;
    startBtn.style.display = 'block';
    stopBtn.style.display = 'none';
  }
});

document.getElementById('stopBtn').addEventListener('click', () => {
  stopRequested = true;
  document.getElementById('status').textContent = 'Stoppen na huidige pagina...';
});

function loadImage(dataUrl) {
  return new Promise((resolve) => {
    const img = new Image();
    img.onload = () => resolve(img);
    img.src = dataUrl;
  });
}

async function cropImage(dataUrl, cropInfo, usePng, quality) {
  const img = await loadImage(dataUrl);
  
  const canvas = document.createElement('canvas');
  const ctx = canvas.getContext('2d');
  
  // Calculate crop coordinates (account for device pixel ratio)
  const dpr = cropInfo.devicePixelRatio;
  const cropX = Math.round(cropInfo.x * dpr);
  const cropY = Math.round(cropInfo.y * dpr);
  const cropWidth = Math.round(cropInfo.width * dpr);
  const cropHeight = Math.round(cropInfo.height * dpr);
  
  canvas.width = cropWidth;
  canvas.height = cropHeight;
  
  // Draw cropped portion
  ctx.drawImage(
    img,
    cropX, cropY, cropWidth, cropHeight,  // Source rectangle
    0, 0, cropWidth, cropHeight            // Destination rectangle
  );
  
  if (usePng) {
    return canvas.toDataURL('image/png');
  } else {
    return canvas.toDataURL('image/jpeg', quality);
  }
}

// Accordion functionality
document.addEventListener('DOMContentLoaded', () => {
  const accordion = document.querySelector('.accordion');
  if (accordion) {
    accordion.addEventListener('click', function() {
      this.classList.toggle('active');
      const panel = document.getElementById('advancedPanel');
      panel.classList.toggle('show');
    });
  }
  
  // Auto-detect total pages from the current page
  chrome.tabs.query({ active: true, currentWindow: true }, async (tabs) => {
    if (tabs[0]) {
      try {
        const result = await chrome.scripting.executeScript({
          target: { tabId: tabs[0].id },
          func: () => {
            const iframe = document.getElementById('ScormContent');
            if (iframe && iframe.contentDocument) {
              const navLabel = iframe.contentDocument.querySelector('.navpagelabel');
              if (navLabel) {
                // Extract number from "van 33" format
                const match = navLabel.textContent.match(/van\s+(\d+)/);
                if (match) {
                  return parseInt(match[1]);
                }
              }
            }
            return null;
          }
        });
        
        if (result && result[0] && result[0].result) {
          document.getElementById('totalPages').value = result[0].result;
        }
      } catch (error) {
        console.log('Could not auto-detect total pages:', error);
      }
    }
  });
});
