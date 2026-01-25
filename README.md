# SCORM PDF Exporter v8

## What's New in v8

This version crops screenshots to capture **only the `outerClipDiv` content** inside the SCORM iframe, removing all player UI elements including:
- Top navigation bar
- Player chrome
- All UI elements outside the actual course content

The internal menus within the course (if any) will remain visible.

## Setup Instructions

### Step 1: Download jsPDF

You need to add the jsPDF library to the extension folder:

1. Download jsPDF from: https://cdnjs.cloudflare.com/ajax/libs/jspdf/2.5.1/jspdf.umd.min.js
2. Save it as `jspdf.umd.min.js` in this folder (scorm-exporter-v8)

### Step 2: Install the Extension

1. Go to `chrome://extensions/`
2. Enable "Developer mode" (toggle in top right)
3. Click "Load unpacked"
4. Select the `scorm-exporter-v8` folder

### Step 3: Use the Extension

1. Navigate to your SCORM course (page 1)
2. Click the extension icon in your toolbar
3. Configure settings:
   - **Total pages**: Number of pages to capture
   - **Delay**: Time between page transitions (1500ms recommended)
   - **Quality**: JPEG quality or PNG for lossless
   - **Resolution**: Higher = sharper but larger files (2x recommended)
   - **Crop to content div**: ✅ Check this to capture only outerClipDiv
4. Click "Start Capture"

## Features

- ✅ Crops to `outerClipDiv` for clean content-only screenshots
- ✅ Stop button to halt capture mid-process
- ✅ High-resolution capture (up to 3x zoom)
- ✅ Quality settings from Low to Maximum (PNG)
- ✅ Progress bar
- ✅ Automatic PDF generation

## Folder Structure

```
scorm-exporter-v8/
├── manifest.json
├── popup.html
├── popup.js
├── jspdf.umd.min.js  ← YOU NEED TO ADD THIS FILE
└── README.md
```
