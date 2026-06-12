/**
 * PO Info Extractor - Frontend Backend Integration
 * Connects the HTML5/CSS3 frontend to the Flask API (port 5000) for real-time document parsing,
 * database query logs, filtering, and Blob exports. Includes fallbacks for API connection failures.
 */

// 1. JavaScript Backend API Constant
const API_BASE = "http://127.0.0.1:5000";

// Global lists to hold active session database records for exports and filtering
let historyHeaders = [];
let historyItems = [];
let selectedFiles = [];
let extractionInProgress = false;

document.addEventListener('DOMContentLoaded', () => {
  // Initialize dynamic canvas background
  initStarsBackground();
  
  // Set up tab-switching for sidebar pages
  initNavigation();
  
  // Set up live drag-and-drop file processing
  initDropzone();
  
  // Initialize results sub-tab navigation
  initResultsSubTabs();
  
  // Initialize History and Download pages by loading data from Flask database API
  loadDatabaseHistory();
  
  // Connect watcher code copy button
  initCommandCopy();
  
  // Bind CSV/JSON download triggers
  initDataExports();
});

/* ==========================================================================
   2. Canvas Starry Glow Background
   ========================================================================== */
function initStarsBackground() {
  const container = document.getElementById('stars-container');
  if (!container) return;

  const starCount = 80;
  for (let i = 0; i < starCount; i++) {
    const star = document.createElement('div');
    star.classList.add('star');
    
    // Positioning
    const x = Math.random() * 100;
    const y = Math.random() * 100;
    
    // Size distribution (1px to 3px)
    const size = Math.random() * 2 + 1;
    
    // Keyframe rate variations
    const delay = Math.random() * 5;
    const duration = Math.random() * 3 + 2;

    star.style.left = `${x}%`;
    star.style.top = `${y}%`;
    star.style.width = `${size}px`;
    star.style.height = `${size}px`;
    star.style.animationDelay = `${delay}s`;
    star.style.animationDuration = `${duration}s`;

    container.appendChild(star);
  }
}

/* ==========================================================================
   3. Sidebar Navigation Tab Controller
   ========================================================================== */
function initNavigation() {
  const navItems = document.querySelectorAll('.nav-item');
  const sections = document.querySelectorAll('.tab-content');

  navItems.forEach(item => {
    item.addEventListener('click', () => {
      const targetTab = item.getAttribute('data-tab');

      // Update active nav button
      navItems.forEach(nav => nav.classList.remove('active'));
      item.classList.add('active');

      // Swap section viewport
      sections.forEach(section => {
        if (section.id === targetTab) {
          section.classList.add('active');
        } else {
          section.classList.remove('active');
        }
      });
      
      // Auto-refresh history records whenever user clicks into the History or Download tabs
      if (targetTab === 'history-section' || targetTab === 'download-section') {
        loadDatabaseHistory();
      }
    });
  });
}

/* ==========================================================================
   4. Results Sub-Tab Controller
   ========================================================================== */
function initResultsSubTabs() {
  const tabButtons = document.querySelectorAll('.results-tab-btn');
  const tabContents = document.querySelectorAll('.results-tab-content');

  tabButtons.forEach(btn => {
    btn.addEventListener('click', () => {
      const targetSubTab = btn.getAttribute('data-results-tab');

      // Set active button
      tabButtons.forEach(b => b.classList.remove('active'));
      btn.classList.add('active');

      // Show matching sub-tab content
      tabContents.forEach(content => {
        if (content.id === `results-tab-${targetSubTab}`) {
          content.classList.add('active');
        } else {
          content.classList.remove('active');
        }
      });
    });
  });
}

/* ==========================================================================
   5. Drag & Drop Live File Uploader (Manual Upload)
   ========================================================================== */
function initDropzone() {
  const dropzone = document.getElementById('dropzone');
  const fileInput = document.getElementById('file-input');
  const fileListContainer = document.getElementById('file-list-container');
  const fileListTbody = document.getElementById('file-list-tbody');
  const startBtn = document.getElementById('start-extraction-btn');
  const clearBtn = document.getElementById('clear-files-btn');
  const loadingCard = document.getElementById('extraction-loading-card');
  const resultsContainer = document.getElementById('extraction-results-container');
  const resetUploadBtn = document.getElementById('reset-upload-btn');

  if (!dropzone || !fileInput) return;

  // Clicking dropzone triggers local file prompt
  dropzone.addEventListener('click', (e) => {
    if (e.target !== fileInput && !e.target.classList.contains('browse-link')) {
      fileInput.click();
    }
  });

  // Highlight border on hover drag
  ['dragenter', 'dragover'].forEach(eventName => {
    dropzone.addEventListener(eventName, (e) => {
      e.preventDefault();
      dropzone.classList.add('dragover');
    }, false);
  });

  ['dragleave', 'drop'].forEach(eventName => {
    dropzone.addEventListener(eventName, (e) => {
      e.preventDefault();
      dropzone.classList.remove('dragover');
    }, false);
  });

  // Catch dropped files
  dropzone.addEventListener('drop', (e) => {
    const dt = e.dataTransfer;
    const files = Array.from(dt.files);
    processSelectedFiles(files, true);
  });

  // Catch selected files
  fileInput.addEventListener('change', (e) => {
    const files = Array.from(e.target.files);
    processSelectedFiles(files, true);
  });

  function processSelectedFiles(files, autoStart = false) {
    const pdfs = files.filter(file => file.type === 'application/pdf' || file.name.toLowerCase().endsWith('.pdf'));
    
    if (pdfs.length === 0) {
      alert('Please upload PDF files only.');
      return;
    }

    pdfs.forEach(file => {
      if (!selectedFiles.some(f => f.name === file.name)) {
        selectedFiles.push(file);
      }
    });

    renderFileList();

    if (autoStart && selectedFiles.length > 0) {
      setTimeout(beginExtraction, 250);
    }
  }

  function renderFileList() {
    fileListTbody.innerHTML = '';
    
    if (selectedFiles.length > 0) {
      fileListContainer.classList.remove('hidden');
      dropzone.classList.add('hidden');
    } else {
      fileListContainer.classList.add('hidden');
      dropzone.classList.remove('hidden');
    }

    selectedFiles.forEach((file, index) => {
      const row = document.createElement('tr');
      const sizeKB = (file.size / 1024).toFixed(1);
      
      row.innerHTML = `
        <td>${file.name}</td>
        <td>${sizeKB} KB</td>
        <td><span class="status-badge warning" style="background: rgba(148,163,184,0.06); color: var(--text-muted); border-color: transparent;">Ready</span></td>
        <td class="text-right">
          <button class="btn-icon-danger" data-index="${index}" aria-label="Remove item">
            <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2">
              <path stroke-linecap="round" stroke-linejoin="round" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
            </svg>
          </button>
        </td>
      `;

      row.querySelector('.btn-icon-danger').addEventListener('click', (e) => {
        e.stopPropagation();
        const removeIdx = parseInt(e.currentTarget.getAttribute('data-index'));
        selectedFiles.splice(removeIdx, 1);
        renderFileList();
      });

      fileListTbody.appendChild(row);
    });
  }

  // Clear file caches
  clearBtn.addEventListener('click', () => {
    if (extractionInProgress) return;
    selectedFiles = [];
    fileInput.value = '';
    renderFileList();
  });

  function beginExtraction() {
    if (selectedFiles.length === 0 || extractionInProgress) return;

    extractionInProgress = true;
    startBtn.disabled = true;
    startBtn.textContent = 'Extracting...';

    fileListContainer.classList.add('hidden');
    loadingCard.classList.remove('hidden');
    resultsContainer.classList.add('hidden');
    
    // Hide global alert banner before request runs
    document.getElementById('connection-error-alert').classList.add('hidden');
    
    executeLiveExtraction();
  }

  // Action Button: Start Extraction (Calls Live POST /extract API)
  startBtn.addEventListener('click', beginExtraction);

  // Process new files reset trigger
  resetUploadBtn.addEventListener('click', () => {
    if (extractionInProgress) return;
    resultsContainer.classList.add('hidden');
    selectedFiles = [];
    fileInput.value = '';
    startBtn.disabled = false;
    startBtn.textContent = 'Start Extraction';
    renderFileList();
    dropzone.classList.remove('hidden');
  });
}

/* ==========================================================================
   6. Live Extraction Runner (fetch POST /extract)
   ========================================================================== */
function executeLiveExtraction() {
  const progressBar = document.getElementById('progress-bar-fill');
  const stepTitle = document.getElementById('loading-step-title');
  const stepDesc = document.getElementById('loading-step-desc');
  const consoleLogs = document.getElementById('console-logs');
  const loadingCard = document.getElementById('extraction-loading-card');
  const resultsContainer = document.getElementById('extraction-results-container');
  const fileListContainer = document.getElementById('file-list-container');

  consoleLogs.innerHTML = '';
  progressBar.style.width = '0%';

  function printLog(text, type = 'info') {
    const logTime = new Date().toISOString().split('T')[1].slice(0, 8);
    const logEntry = document.createElement('div');
    logEntry.classList.add('log-entry');
    
    let prefix = '[INFO]';
    if (type === 'success') prefix = '[SUCCESS]';
    if (type === 'warning') prefix = '[WARN]';
    if (type === 'error') prefix = '[ERROR]';

    logEntry.innerHTML = `<span class="log-entry time">[${logTime}]</span> <span class="log-entry ${type}">${prefix} ${text}</span>`;
    consoleLogs.appendChild(logEntry);
    consoleLogs.scrollTop = consoleLogs.scrollHeight;
  }

  printLog('Initializing live PDF extraction pipeline...');
  progressBar.style.width = '10%';

  // Step 1: Prepare FormData payload
  const formData = new FormData();
  selectedFiles.forEach(file => {
    formData.append('files', file);
    printLog(`Loaded file for upload: ${file.name} (${(file.size / 1024).toFixed(1)} KB)`);
  });

  // Simulated logging sequences during connection wait time
  let logTick = 1;
  const simulatedLogger = setInterval(() => {
    logTick++;
    if (logTick === 2) {
      stepTitle.textContent = 'Uploading Documents';
      stepDesc.textContent = 'Uploading files to the Python Flask API...';
      printLog('Sending multipart form-data payload to POST /extract...');
      progressBar.style.width = '25%';
    } else if (logTick === 4) {
      stepTitle.textContent = 'Running Optical Character Recognition';
      stepDesc.textContent = 'Parsing structural layouts and extracting text content.';
      printLog('Flask API processing files: extracting raw text structures...');
      progressBar.style.width = '45%';
    } else if (logTick === 6) {
      stepTitle.textContent = 'Extracting AI Schema Details';
      stepDesc.textContent = 'Running LLM text parsing to extract header metadata and items.';
      printLog('AI Model Parsing fields: matching PO headers, state codes, and items...');
      progressBar.style.width = '70%';
    } else if (logTick === 8) {
      stepTitle.textContent = 'Validating Extracted Output';
      stepDesc.textContent = 'Performing calculations checks and saving to MySQL.';
      printLog('Performing validation checks. Saving results to MySQL DB schema...');
      progressBar.style.width = '85%';
    }
  }, 1000);

  // Step 2: Make actual API Call to Flask backend
  // We send include_debug=true so we receive warnings and status codes for UI rendering
  fetch(`${API_BASE}/extract?include_debug=true`, {
    method: 'POST',
    body: formData
  })
  .then(response => {
    clearInterval(simulatedLogger);
    if (!response.ok) {
      throw new Error(`HTTP error! status: ${response.status}`);
    }
    return response.json();
  })
  .then(data => {
    progressBar.style.width = '100%';
    stepTitle.textContent = 'Extraction Complete';
    stepDesc.textContent = 'Processed files successfully.';
    printLog(`API returned status 200. ${data.message || 'Complete.'}`, 'success');

    setTimeout(() => {
      try {
        // Render the returned backend payload across the results tabs before swapping UI.
        renderExtractionResultsUI(data);
        loadingCard.classList.add('hidden');
        resultsContainer.classList.remove('hidden');
        printLog('Rendered extraction result tables.', 'success');
        
        // Re-trigger database history load in background to update main table
        loadDatabaseHistory();
      } catch (renderError) {
        printLog(`Result render failed: ${renderError.message}`, 'error');
        const alertBanner = document.getElementById('connection-error-alert');
        alertBanner.querySelector('.alert-text').textContent = 'Extraction finished, but the result view could not be rendered. Please check the browser console.';
        alertBanner.classList.remove('hidden');
      } finally {
        extractionInProgress = false;
        const startBtn = document.getElementById('start-extraction-btn');
        startBtn.disabled = false;
        startBtn.textContent = 'Start Extraction';
      }
    }, 800);
  })
  .catch(error => {
    clearInterval(simulatedLogger);
    printLog(`Failed to connect to API: ${error.message}`, 'error');
    printLog('Please make sure that flask_api.py is running on port 5000.', 'error');
    
    setTimeout(() => {
      loadingCard.classList.add('hidden');
      fileListContainer.classList.remove('hidden');
      
      // Render standard user friendly alert banner
      const alertBanner = document.getElementById('connection-error-alert');
      alertBanner.querySelector('.alert-text').textContent = 'Unable to extract data. Please make sure the Flask server is running and try again.';
      alertBanner.classList.remove('hidden');
      extractionInProgress = false;
      const startBtn = document.getElementById('start-extraction-btn');
      startBtn.disabled = false;
      startBtn.textContent = 'Start Extraction';
      window.scrollTo({ top: 0, behavior: 'smooth' });
    }, 1500);
  });
}

/* ==========================================================================
   7. Populate Extraction Results Tabs UI (Real API Data)
   ========================================================================== */
function renderExtractionResultsUI(apiResponse) {
  const documents = apiResponse.documents || [];
  
  // Calculate summary metrics
  const totalFiles = documents.length;
  let completedCount = 0;
  let reviewCount = 0;
  let lineItemsCount = 0;

  // Clear tables
  const overviewTbody = document.getElementById('overview-tbody');
  const poDataTbody = document.getElementById('po-data-tbody');
  const lineItemsTbody = document.getElementById('line-items-tbody');
  const accordionContainer = document.getElementById('filewise-accordion-container');

  overviewTbody.innerHTML = '';
  poDataTbody.innerHTML = '';
  lineItemsTbody.innerHTML = '';
  accordionContainer.innerHTML = '';

  // Filter input bind
  const lineItemsSearch = document.getElementById('line-items-search');
  lineItemsSearch.value = '';

  documents.forEach((doc, docIndex) => {
    const file_name = doc.file_name || 'extracted_file.pdf';
    const headerData = doc.data || {};
    const items = doc.items || [];
    const debugInfo = doc.debug || {};
    
    // Status and Warnings
    const rawStatus = debugInfo.extraction_status || 'Completed';
    const statusText = rawStatus === 'Completed' ? 'Extracted' : 
                       rawStatus === 'Failed' ? 'Failed' : 'Needs Review';
    const statusClass = statusText.toLowerCase().replace(' ', '-');
    const warnings = debugInfo.warnings || [];

    if (rawStatus === 'Completed') {
      completedCount++;
    } else {
      reviewCount++;
    }
    lineItemsCount += items.length;

    // A. Populate Tab 1: Overview Table Row
    const overviewRow = document.createElement('tr');
    overviewRow.innerHTML = `
      <td><strong>${file_name}</strong></td>
      <td>${headerData.po_date || '—'}</td>
      <td>${headerData.buyer_name || '—'}</td>
      <td>${headerData.billing_state || '—'}</td>
      <td>${headerData.billing_gst_number || '—'}</td>
      <td><span class="status-badge ${statusClass}">${statusText}</span></td>
    `;
    overviewTbody.appendChild(overviewRow);

    // B. Populate Tab 2: PO Header Data Row
    const headerRow = document.createElement('tr');
    const addressString = headerData.billing_address || '—';
    headerRow.innerHTML = `
      <td><strong>${file_name}</strong></td>
      <td><code style="font-family: var(--font-mono); color: var(--accent-cyan); font-size: 0.85rem;">${headerData.po_number || '—'}</code></td>
      <td>${headerData.po_date || '—'}</td>
      <td>${headerData.buyer_name || '—'}</td>
      
      <td>${headerData.billing_state || '—'}</td>
      <td>${headerData.billing_pincode || '—'}</td>
      <td>${headerData.billing_gst_number || '—'}</td>
    `;
    poDataTbody.appendChild(headerRow);

    // C. Populate Tab 3: Line Items (Combine all files into flat list)
    items.forEach(item => {
      const lineRow = document.createElement('tr');
      lineRow.classList.add('item-row-entry');
      lineRow.setAttribute('data-desc', (item.item_description || '').toLowerCase());
      
      const qtyVal = item.quantity ? parseFloat(item.quantity).toFixed(2) : '0.00';
      const priceVal = item.unit_price ? parseFloat(item.unit_price).toLocaleString('en-IN', { minimumFractionDigits: 2 }) : '0.00';
      const taxVal = item.tax_percent ? parseFloat(item.tax_percent).toFixed(2) : '0.00';
      const totalVal = item.line_total ? parseFloat(item.line_total).toLocaleString('en-IN', { minimumFractionDigits: 2 }) : '0.00';

      lineRow.innerHTML = `
        <td style="color: var(--text-muted); font-size: 0.82rem;">${file_name}</td>
        <td><code style="font-family: var(--font-mono); font-size: 0.85rem;">${headerData.po_number || '—'}</code></td>
        <td>${item.item_no || '—'}</td>
        <td><strong>${item.item_name || ''}</strong><br><small style="color: var(--text-muted);">${item.item_description || ''}</small></td>
        <td>${item.hsn_sac || '—'}</td>
        <td>${qtyVal}</td>
        <td>${item.uom || '—'}</td>
        <td>Rs. ${priceVal}</td>
        <td>${taxVal}%</td>
        <td style="color: var(--accent-cyan); font-weight: 600;">Rs. ${totalVal}</td>
      `;
      lineItemsTbody.appendChild(lineRow);
    });

    // D. Populate Tab 4: File-wise Review (Accordion structure)
    const accordionContainerItem = document.createElement('div');
    accordionContainerItem.classList.add('expander-container');
    if (docIndex === 0) accordionContainerItem.classList.add('active'); // Expand first file by default

    // Accordion Header
    const accordionHeader = document.createElement('div');
    accordionHeader.classList.add('expander-header');
    accordionHeader.innerHTML = `
      <div class="expander-title-group">
        <span class="expander-title">${file_name}</span>
        <span class="status-badge ${statusClass}">${statusText}</span>
      </div>
      <div class="expander-icon">
        <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2">
          <path stroke-linecap="round" stroke-linejoin="round" d="M19 9l-7 7-7-7" />
        </svg>
      </div>
    `;

    // Accordion Content body
    const accordionContent = document.createElement('div');
    accordionContent.classList.add('expander-content');
    
    // File warning box HTML
    let warningsBoxHTML = '';
    if (warnings.length > 0) {
      warningsBoxHTML = `
        <div class="result-warning-box">
          <h5 class="warning-box-title">Validation Warning</h5>
          <p class="warning-box-text">${warnings.join('; ')}</p>
        </div>
      `;
    }

    // Build specific line item rows for this file
    let fileItemsRows = '';
    if (items.length > 0) {
      items.forEach(item => {
        const qtyVal = item.quantity ? parseFloat(item.quantity).toFixed(2) : '0.00';
        const priceVal = item.unit_price ? parseFloat(item.unit_price).toLocaleString('en-IN', { minimumFractionDigits: 2 }) : '0.00';
        const totalVal = item.line_total ? parseFloat(item.line_total).toLocaleString('en-IN', { minimumFractionDigits: 2 }) : '0.00';

        fileItemsRows += `
          <tr>
            <td>${item.item_no || '—'}</td>
            <td><strong>${item.item_name || ''}</strong><br><small style="color: var(--text-muted);">${item.item_description || ''}</small></td>
            <td>${item.hsn_sac || '—'}</td>
            <td>${qtyVal}</td>
            <td>${item.uom || '—'}</td>
            <td>Rs. ${priceVal}</td>
            <td>Rs. ${totalVal}</td>
          </tr>
        `;
      });
    } else {
      fileItemsRows = '<tr><td colspan="7" style="text-align: center; color: var(--text-muted);">No line items found for this document.</td></tr>';
    }

    // Individual download JSON template
    const individualJSONData = {
      file_name: file_name,
      data: headerData,
      items: items
    };

    // Construct accordion body
    accordionContent.innerHTML = `
      ${warningsBoxHTML}
      <div class="split-layout-grid">
        <!-- Billing Information -->
        <div class="card glassmorphism" style="padding: 1.5rem;">
          <h5 class="container-title" style="font-size: 1.05rem; margin-bottom: 1rem; border-bottom: 1px solid var(--border); padding-bottom: 0.5rem;">Billing Information</h5>
          <table class="data-table" style="font-size: 0.85rem;">
            <tbody>
              <tr><td style="color: var(--text-muted); font-weight: 600; width: 35%;">Buyer Name</td><td>${headerData.buyer_name || '—'}</td></tr>
              <tr><td style="color: var(--text-muted); font-weight: 600;">Billing Address</td><td>${addressString}</td></tr>
              <tr><td style="color: var(--text-muted); font-weight: 600;">State</td><td>${headerData.billing_state || '—'}</td></tr>
              <tr><td style="color: var(--text-muted); font-weight: 600;">Pincode</td><td>${headerData.billing_pincode || '—'}</td></tr>
              <tr><td style="color: var(--text-muted); font-weight: 600;">GST Number</td><td>${headerData.billing_gst_number || '—'}</td></tr>
            </tbody>
          </table>
        </div>
        
        <!-- Purchase Order Details -->
        <div class="card glassmorphism" style="padding: 1.5rem;">
          <h5 class="container-title" style="font-size: 1.05rem; margin-bottom: 1rem; border-bottom: 1px solid var(--border); padding-bottom: 0.5rem;">Purchase Order Details</h5>
          <table class="data-table" style="font-size: 0.85rem;">
            <tbody>
              <tr><td style="color: var(--text-muted); font-weight: 600; width: 35%;">PO Number</td><td><code style="font-family: var(--font-mono); color: var(--accent-cyan);">${headerData.po_number || '—'}</code></td></tr>
              <tr><td style="color: var(--text-muted); font-weight: 600;">PO Date</td><td>${headerData.po_date || '—'}</td></tr>
              <tr><td style="color: var(--text-muted); font-weight: 600;">Vendor Name</td><td>${headerData.vendor_name || '—'}</td></tr>
              <tr><td style="color: var(--text-muted); font-weight: 600;">Vendor GSTIN</td><td>${headerData.vendor_gst_number || '—'}</td></tr>
              <tr><td style="color: var(--text-muted); font-weight: 600; font-size: 0.9rem;">Grand Total</td><td style="color: var(--accent-cyan); font-weight: 700; font-size: 0.9rem;">Rs. ${headerData.total_amount ? parseFloat(headerData.total_amount).toLocaleString('en-IN', { minimumFractionDigits: 2 }) : '0.00'}</td></tr>
            </tbody>
          </table>
        </div>
      </div>

      <!-- File specific table -->
      <h5 class="container-title" style="font-size: 1.05rem; margin-bottom: 1rem;">Line Items</h5>
      <div class="table-responsive" style="margin-bottom: 2rem;">
        <table class="data-table" style="font-size: 0.88rem;">
          <thead>
            <tr>
              <th>No</th>
              <th>Item Name & Description</th>
              <th>HSN/SAC</th>
              <th>Qty</th>
              <th>UOM</th>
              <th>Unit Price</th>
              <th>Line Total</th>
            </tr>
          </thead>
          <tbody>
            ${fileItemsRows}
          </tbody>
        </table>
      </div>

      <!-- File specific download buttons -->
      <div class="upload-actions" style="margin-top: 0px; justify-content: flex-start; gap: 1rem;">
        <button class="btn btn-secondary btn-sm file-dl-header" data-filename="${file_name}" style="background: rgba(79, 124, 255, 0.08); border-color: rgba(79, 124, 255, 0.2);">Download Header CSV</button>
        <button class="btn btn-secondary btn-sm file-dl-items" data-filename="${file_name}" ${items.length === 0 ? 'disabled' : ''} style="background: rgba(24, 200, 255, 0.08); border-color: rgba(24, 200, 255, 0.2);">Download Items CSV</button>
        <button class="btn btn-secondary btn-sm file-dl-json" data-filename="${file_name}" style="background: rgba(124, 58, 237, 0.08); border-color: rgba(124, 58, 237, 0.2);">Download JSON</button>
      </div>
    `;

    // Toggle expander event
    accordionHeader.addEventListener('click', () => {
      const isActive = accordionContainerItem.classList.contains('active');
      
      // Close all accordion panels
      document.querySelectorAll('.expander-container').forEach(c => c.classList.remove('active'));
      
      // Toggle current panel
      if (!isActive) {
        accordionContainerItem.classList.add('active');
      }
    });

    // Individual download triggers using JavaScript Blobs
    accordionContent.querySelector('.file-dl-header').addEventListener('click', () => {
      const csvData = generateCSVForSingleHeader(file_name, headerData);
      triggerFileBlobDownload(csvData, `${file_name.replace('.pdf', '')}_po_header.csv`, 'text/csv;charset=utf-8;');
    });

    if (items.length > 0) {
      accordionContent.querySelector('.file-dl-items').addEventListener('click', () => {
        const csvData = generateCSVForSingleItems(file_name, headerData.po_number, items);
        triggerFileBlobDownload(csvData, `${file_name.replace('.pdf', '')}_po_items.csv`, 'text/csv;charset=utf-8;');
      });
    }

    accordionContent.querySelector('.file-dl-json').addEventListener('click', () => {
      const jsonData = JSON.stringify(individualJSONData, null, 2);
      triggerFileBlobDownload(jsonData, `${file_name.replace('.pdf', '')}.json`, 'application/json;charset=utf-8;');
    });

    accordionContainerItem.appendChild(accordionHeader);
    accordionContainerItem.appendChild(accordionContent);
    accordionContainer.appendChild(accordionContainerItem);
  });

  // Update metrics UI
  document.getElementById('metric-total-files').textContent = totalFiles;
  document.getElementById('metric-completed-files').textContent = completedCount;
  document.getElementById('metric-review-files').textContent = reviewCount;
  document.getElementById('metric-total-items').textContent = lineItemsCount;

  // Bind the keyup search event on Line Items Tab
  lineItemsSearch.addEventListener('keyup', (e) => {
    const query = e.target.value.toLowerCase().trim();
    const rows = document.querySelectorAll('.item-row-entry');
    
    rows.forEach(row => {
      const text = row.getAttribute('data-desc');
      if (query === '' || text.includes(query)) {
        row.classList.remove('hidden');
      } else {
        row.classList.add('hidden');
      }
    });
  });
}

// Single file CSV writers
function generateCSVForSingleHeader(filename, header) {
  const fields = ['file_name', 'po_number', 'po_date', 'buyer_name', 'billing_address', 'billing_state', 'billing_pincode', 'billing_gst_number', 'vendor_name', 'vendor_gst_number', 'total_amount'];
  const data = [
    filename,
    header.po_number || '',
    header.po_date || '',
    header.buyer_name || '',
    (header.billing_address || '').replace(/"/g, '""'),
    header.billing_state || '',
    header.billing_pincode || '',
    header.billing_gst_number || '',
    header.vendor_name || '',
    header.vendor_gst_number || '',
    header.total_amount || ''
  ];
  return fields.join(',') + '\n' + data.map(val => `"${val}"`).join(',') + '\n';
}

function generateCSVForSingleItems(filename, poNum, items) {
  const fields = ['file_name', 'po_number', 'item_no', 'item_name', 'item_description', 'hsn_sac', 'quantity', 'uom', 'unit_price', 'tax_percent', 'line_total'];
  let csv = fields.join(',') + '\n';
  items.forEach(item => {
    const row = [
      filename,
      poNum || '',
      item.item_no || '',
      item.item_name || '',
      (item.item_description || '').replace(/"/g, '""'),
      item.hsn_sac || '',
      item.quantity || '0',
      item.uom || '',
      item.unit_price || '0',
      item.tax_percent || '0',
      item.line_total || '0'
    ];
    csv += row.map(val => `"${val}"`).join(',') + '\n';
  });
  return csv;
}

/* ==========================================================================
   8. Fetch Database History (GET /headers and GET /items)
   ========================================================================== */
function loadDatabaseHistory() {
  const tbody = document.getElementById('history-tbody');
  const alertBanner = document.getElementById('connection-error-alert');
  
  if (!tbody) return;

  // Clear UI and fetch
  tbody.innerHTML = '<tr><td colspan="7" style="text-align:center; color: var(--text-muted);">Syncing with MySQL database...</td></tr>';
  
  // Call API /headers and /items endpoints in parallel
  Promise.all([
    fetch(`${API_BASE}/headers?limit=100`).then(r => {
      if (!r.ok) throw new Error();
      return r.json();
    }),
    fetch(`${API_BASE}/items?limit=100`).then(r => {
      if (!r.ok) throw new Error();
      return r.json();
    })
  ])
  .then(([headersPayload, itemsPayload]) => {
    // Hide error banner if connection succeeds
    alertBanner.classList.add('hidden');
    
    // Check returned statuses
    if (headersPayload.success) {
      historyHeaders = headersPayload.data || [];
    } else {
      historyHeaders = [];
      console.warn('API returned failure for header fetch:', headersPayload.message);
    }

    if (itemsPayload.success) {
      historyItems = itemsPayload.items || [];
    } else {
      historyItems = [];
    }

    // Render results
    renderHistoryTableUI();
  })
  .catch(error => {
    console.error('Failed to sync history from API:', error);
    historyHeaders = [];
    historyItems = [];
    
    // Clear rows and display connection failure banner
    tbody.innerHTML = '';
    const tableEl = document.getElementById('history-table');
    const emptyState = document.getElementById('history-empty-state');
    
    tableEl.classList.add('hidden');
    emptyState.classList.remove('hidden');
    
    // Set friendly error details
    document.getElementById('history-empty-desc').textContent = "Unable to load records. Please make sure the Flask server is running.";
    
    // Show alert banner on top
    alertBanner.classList.remove('hidden');
  });
}

function renderHistoryTableUI() {
  const tbody = document.getElementById('history-tbody');
  const emptyState = document.getElementById('history-empty-state');
  const tableEl = document.getElementById('history-table');
  const searchInput = document.getElementById('history-search');
  const statusFilter = document.getElementById('status-filter');

  if (!tbody || !emptyState || !tableEl || !searchInput || !statusFilter) return;

  tbody.innerHTML = '';

  const searchQuery = searchInput.value.toLowerCase().trim();
  const selectedStatus = statusFilter.value;

  // Bind live listeners on inputs if not already done
  if (!searchInput.dataset.bound) {
    searchInput.addEventListener('input', renderHistoryTableUI);
    statusFilter.addEventListener('change', renderHistoryTableUI);
    searchInput.dataset.bound = "true";
  }

  // Filter history records based on inputs
  const filtered = historyHeaders.filter(record => {
    // 1. Status Filter
    let matchesStatus = true;
    if (selectedStatus !== 'all') {
      const dbStatus = (record.extraction_status || '').toLowerCase().replace(' ', '');
      const filterVal = selectedStatus.toLowerCase().replace(' ', '');
      matchesStatus = dbStatus === filterVal;
    }

    // 2. Search Query Filter
    let matchesSearch = true;
    if (searchQuery !== '') {
      matchesSearch = (record.file_name || '').toLowerCase().includes(searchQuery) ||
                      (record.po_number || '').toLowerCase().includes(searchQuery) ||
                      (record.buyer_name || '').toLowerCase().includes(searchQuery) ||
                      (record.vendor_name || '').toLowerCase().includes(searchQuery);
    }

    return matchesStatus && matchesSearch;
  });

  if (filtered.length === 0) {
    tableEl.classList.add('hidden');
    emptyState.classList.remove('hidden');
    document.getElementById('history-empty-desc').textContent = "Try modifying your search or filter options to find matching transactions.";
  } else {
    tableEl.classList.remove('hidden');
    emptyState.classList.add('hidden');

    filtered.forEach(record => {
      const row = document.createElement('tr');
      
      const rawStatus = record.extraction_status || 'Completed';
      const statusText = rawStatus === 'Completed' ? 'Extracted' : 
                         rawStatus === 'Failed' ? 'Failed' : 'Needs Review';
      const statusClass = statusText.toLowerCase().replace(' ', '-');
      
      let amountNum = parseFloat(record.total_amount);
      const amountText = (!isNaN(amountNum) && amountNum > 0) ? 
        `Rs. ${amountNum.toLocaleString('en-IN', { minimumFractionDigits: 2 })}` : 
        '—';

      // Format created timestamp
      let formattedTime = record.created_at || '—';
      if (formattedTime.includes('T')) {
        formattedTime = formattedTime.replace('T', ' ').slice(0, 19);
      }

      row.innerHTML = `
        <td><strong>${record.file_name || '—'}</strong></td>
        <td><code style="font-family: var(--font-mono); color: var(--accent-cyan); font-size: 0.85rem;">${record.po_number || '—'}</code></td>
        <td>${record.po_date || '—'}</td>
        <td>${record.vendor_name || '—'}</td>
        <td>${amountText}</td>
        <td><span class="status-badge ${statusClass}">${statusText}</span></td>
        <td style="color: var(--text-muted); font-size: 0.82rem;">${formattedTime}</td>
      `;

      tbody.appendChild(row);
    });
  }
}

/* ==========================================================================
   9. Watcher Command Clipboard Copy
   ========================================================================== */
function initCommandCopy() {
  const copyBtn = document.getElementById('copy-command-btn');
  const commandText = document.getElementById('command-text');
  const tooltip = document.getElementById('copy-tooltip');

  if (!copyBtn || !commandText || !tooltip) return;

  copyBtn.addEventListener('click', () => {
    const textToCopy = commandText.textContent;
    
    navigator.clipboard.writeText(textToCopy)
      .then(() => {
        tooltip.textContent = 'Copied!';
        setTimeout(() => {
          tooltip.textContent = 'Copy';
        }, 1500);
      })
      .catch(err => {
        console.error('Failed to copy CLI command: ', err);
      });
  });
}

/* ==========================================================================
   10. Combined Exports Generator (CSV / JSON via Blob)
   ========================================================================== */
function initDataExports() {
  const downloadHeaderCsvBtn = document.getElementById('download-header-csv-btn');
  const downloadItemsCsvBtn = document.getElementById('download-items-csv-btn');
  const downloadFullJsonBtn = document.getElementById('download-full-json-btn');

  if (!downloadHeaderCsvBtn || !downloadItemsCsvBtn || !downloadFullJsonBtn) return;

  // 1. Export Header Table CSV
  downloadHeaderCsvBtn.addEventListener('click', () => {
    if (historyHeaders.length === 0) {
      alert("No extraction records available in the history database to download.");
      return;
    }

    const headers = [
      'id', 'file_name', 'po_number', 'po_date', 'buyer_name',
      'billing_address', 'billing_state', 'billing_pincode', 'billing_gst_number',
      'vendor_name', 'vendor_gst_number', 'total_amount', 'extraction_status', 'warnings', 'created_at'
    ];

    let csvContent = headers.join(',') + '\n';

    historyHeaders.forEach((record, index) => {
      const row = [
        index + 1,
        `"${record.file_name || ''}"`,
        `"${record.po_number || ''}"`,
        `"${record.po_date || ''}"`,
        `"${record.buyer_name || ''}"`,
        `"${(record.billing_address || '').replace(/"/g, '""')}"`,
        `"${record.billing_state || ''}"`,
        `"${record.billing_pincode || ''}"`,
        `"${record.billing_gst_number || ''}"`,
        `"${record.vendor_name || ''}"`,
        `"${record.vendor_gst_number || ''}"`,
        record.total_amount || '0.00',
        `"${record.extraction_status || ''}"`,
        `"${(record.warnings || '').replace(/"/g, '""')}"`,
        `"${record.created_at || ''}"`
      ];
      csvContent += row.join(',') + '\n';
    });

    triggerFileBlobDownload(csvContent, 'po_headers.csv', 'text/csv;charset=utf-8;');
  });

  // 2. Export Line Items Table CSV
  downloadItemsCsvBtn.addEventListener('click', () => {
    if (historyItems.length === 0) {
      alert("No extracted line-item records available in the database to download.");
      return;
    }

    const headers = [
      'id', 'file_name', 'po_number', 'item_no', 'item_name',
      'item_description', 'hsn_sac', 'quantity', 'uom', 'unit_price', 'tax_percent', 'line_total'
    ];

    let csvContent = headers.join(',') + '\n';

    historyItems.forEach((item, index) => {
      const row = [
        index + 1,
        `"${item.file_name || ''}"`,
        `"${item.po_number || ''}"`,
        `"${item.item_no || ''}"`,
        `"${(item.item_name || '').replace(/"/g, '""')}"`,
        `"${(item.item_description || '').replace(/"/g, '""')}"`,
        `"${item.hsn_sac || ''}"`,
        item.quantity || '0',
        `"${item.uom || ''}"`,
        item.unit_price || '0.00',
        item.tax_percent || '0.00',
        item.line_total || '0.00'
      ];
      csvContent += row.join(',') + '\n';
    });

    triggerFileBlobDownload(csvContent, 'po_items.csv', 'text/csv;charset=utf-8;');
  });

  // 3. Export full nested JSON schema bundle
  downloadFullJsonBtn.addEventListener('click', () => {
    if (historyHeaders.length === 0) {
      alert("No extraction records available in the history database to download.");
      return;
    }

    const fullJSONData = historyHeaders.map((record, index) => {
      // Find matching items for this header file_name + po_number
      const matchingItems = historyItems.filter(item => 
        (item.file_name === record.file_name) && (item.po_number === record.po_number)
      );

      return {
        id: index + 1,
        file_name: record.file_name,
        po_number: record.po_number,
        po_date: record.po_date,
        buyer_name: record.buyer_name,
        billing_address: record.billing_address,
        billing_state: record.billing_state,
        billing_pincode: record.billing_pincode,
        billing_gst_number: record.billing_gst_number,
        vendor_name: record.vendor_name,
        vendor_gst_number: record.vendor_gst_number,
        total_amount: record.total_amount,
        extraction_status: record.extraction_status,
        warnings: record.warnings,
        created_at: record.created_at,
        line_items: matchingItems.map(item => ({
          item_no: item.item_no,
          item_name: item.item_name,
          item_description: item.item_description,
          hsn_sac: item.hsn_sac,
          quantity: item.quantity,
          uom: item.uom,
          unit_price: item.unit_price,
          tax_percent: item.tax_percent,
          line_total: item.line_total
        }))
      };
    });

    const jsonContent = JSON.stringify(fullJSONData, null, 2);
    triggerFileBlobDownload(jsonContent, 'all_extractions.json', 'application/json;charset=utf-8;');
  });
}

function triggerFileBlobDownload(content, filename, contentType) {
  const blob = new Blob([content], { type: contentType });
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  
  link.setAttribute('href', url);
  link.setAttribute('download', filename);
  link.style.visibility = 'hidden';
  
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
  URL.revokeObjectURL(url);
}
