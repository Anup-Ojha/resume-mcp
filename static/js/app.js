// Initialize CodeMirror editor
let editor;
let currentPDFs = [];

// Initialize the application
document.addEventListener('DOMContentLoaded', () => {
    initializeEditor();
    checkHealth();
    loadPDFList();
    setupEventListeners();
    setupTryTabs();
    tryLoadPDFs();
});

// Initialize CodeMirror
function initializeEditor() {
    const textarea = document.getElementById('latex-editor');
    editor = CodeMirror.fromTextArea(textarea, {
        mode: 'stex',
        theme: 'monokai',
        lineNumbers: true,
        lineWrapping: true,
        indentUnit: 4,
        tabSize: 4,
        indentWithTabs: false,
        autofocus: true
    });
    
    // Set default content
    editor.setValue('% Paste your LaTeX code here or click "Load Template"\n\n');
}

// Setup event listeners
function setupEventListeners() {
    document.getElementById('generate-btn').addEventListener('click', generatePDF);
    document.getElementById('load-template-btn').addEventListener('click', loadTemplate);
    document.getElementById('clear-btn').addEventListener('click', clearEditor);
    document.getElementById('refresh-btn').addEventListener('click', loadPDFList);
    
    // Allow Enter key in filename input to trigger generation
    document.getElementById('filename-input').addEventListener('keypress', (e) => {
        if (e.key === 'Enter') {
            generatePDF();
        }
    });
}

// Check system health
async function checkHealth() {
    try {
        const response = await fetch('/api/health');
        const data = await response.json();
        
        const statusEl = document.getElementById('health-status');
        statusEl.className = `health-status ${data.status}`;
        statusEl.textContent = data.latex_installed 
            ? '✅ LaTeX installed and ready' 
            : '⚠️ ' + data.message;
    } catch (error) {
        console.error('Health check failed:', error);
    }
}

// Load default template
async function loadTemplate() {
    try {
        const response = await fetch('/api/template');
        const data = await response.json();
        
        if (data.success) {
            editor.setValue(data.template);
            showMessage('Template loaded successfully', 'success');
        } else {
            showMessage('Template not found', 'error');
        }
    } catch (error) {
        showMessage('Error loading template: ' + error.message, 'error');
    }
}

// Clear editor
function clearEditor() {
    if (confirm('Are you sure you want to clear the editor?')) {
        editor.setValue('');
        editor.focus();
    }
}

// Generate PDF
async function generatePDF() {
    const latexCode = editor.getValue().trim();
    const filename = document.getElementById('filename-input').value.trim() || 'resume';
    
    if (!latexCode) {
        showMessage('Please enter LaTeX code', 'error');
        return;
    }
    
    // Show loading overlay
    showLoading(true);
    
    try {
        const response = await fetch('/api/generate', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                latex_code: latexCode,
                filename: filename
            })
        });
        
        const data = await response.json();
        
        if (response.ok && data.success) {
            showMessage('✅ ' + data.message, 'success');
            loadPDFList(); // Refresh PDF list
            
            // Auto-download the generated PDF
            setTimeout(() => {
                downloadPDF(data.filename);
            }, 500);
        } else {
            showMessage('❌ ' + (data.detail || data.message), 'error');
        }
    } catch (error) {
        showMessage('Error generating PDF: ' + error.message, 'error');
    } finally {
        showLoading(false);
    }
}

// Load PDF list
async function loadPDFList() {
    const listEl = document.getElementById('pdf-list');
    listEl.innerHTML = '<div class="loading">Loading...</div>';
    
    try {
        const response = await fetch('/api/pdfs');
        const data = await response.json();
        
        if (data.success && data.pdfs.length > 0) {
            currentPDFs = data.pdfs;
            renderPDFList(data.pdfs);
        } else {
            listEl.innerHTML = '<div class="loading">No PDFs generated yet</div>';
        }
    } catch (error) {
        listEl.innerHTML = '<div class="loading">Error loading PDFs</div>';
        console.error('Error loading PDFs:', error);
    }
}

// Render PDF list
function renderPDFList(pdfs) {
    const listEl = document.getElementById('pdf-list');
    
    listEl.innerHTML = pdfs.map(pdf => `
        <div class="pdf-item">
            <div class="pdf-info">
                <div class="pdf-name">📄 ${pdf.filename}</div>
                <div class="pdf-meta">${formatFileSize(pdf.size)} • ${formatDate(pdf.modified)}</div>
            </div>
            <div class="pdf-actions">
                <button class="icon-btn" onclick="downloadPDF('${pdf.filename}')" title="Download">
                    ⬇️
                </button>
                <button class="icon-btn delete" onclick="deletePDF('${pdf.filename}')" title="Delete">
                    🗑️
                </button>
            </div>
        </div>
    `).join('');
}

// Download PDF
function downloadPDF(filename) {
    window.open(`/api/pdfs/${filename}`, '_blank');
}

// Delete PDF
async function deletePDF(filename) {
    if (!confirm(`Delete ${filename}?`)) {
        return;
    }
    
    try {
        const response = await fetch(`/api/pdfs/${filename}`, {
            method: 'DELETE'
        });
        
        const data = await response.json();
        
        if (data.success) {
            showMessage('✅ ' + data.message, 'success');
            loadPDFList(); // Refresh list
        } else {
            showMessage('❌ Error deleting PDF', 'error');
        }
    } catch (error) {
        showMessage('Error deleting PDF: ' + error.message, 'error');
    }
}

// Show message
function showMessage(message, type) {
    const messageArea = document.getElementById('message-area');
    messageArea.textContent = message;
    messageArea.className = `message-area ${type} show`;
    
    // Auto-hide after 5 seconds
    setTimeout(() => {
        messageArea.classList.remove('show');
    }, 5000);
}

// Show/hide loading overlay
function showLoading(show) {
    const overlay = document.getElementById('loading-overlay');
    if (show) {
        overlay.classList.remove('hidden');
    } else {
        overlay.classList.add('hidden');
    }
}

// Utility: Format file size
function formatFileSize(bytes) {
    if (bytes < 1024) return bytes + ' B';
    if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB';
    return (bytes / (1024 * 1024)).toFixed(1) + ' MB';
}

// Utility: Format date
function formatDate(timestamp) {
    const date = new Date(timestamp * 1000);
    const now = new Date();
    const diffMs = now - date;
    const diffMins = Math.floor(diffMs / 60000);

    if (diffMins < 1) return 'Just now';
    if (diffMins < 60) return `${diffMins} min ago`;
    if (diffMins < 1440) return `${Math.floor(diffMins / 60)} hours ago`;

    return date.toLocaleDateString();
}

// ── Try It Now ────────────────────────────────────────────────

function setupTryTabs() {
    document.querySelectorAll('.try-tab-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            const tab = btn.dataset.tab;
            document.querySelectorAll('.try-tab-btn').forEach(b => b.classList.remove('active'));
            document.querySelectorAll('.try-panel').forEach(p => p.classList.remove('active'));
            btn.classList.add('active');
            document.getElementById('tab-' + tab).classList.add('active');
            if (tab === 'pdfs') tryLoadPDFs();
        });
    });
}

async function tryCreateResume() {
    const uid      = document.getElementById('create-uid').value.trim();
    const details  = document.getElementById('create-details').value.trim();
    const filename = document.getElementById('create-filename').value.trim();
    const btn      = document.getElementById('create-btn');
    const spinner  = document.getElementById('create-spinner');
    const result   = document.getElementById('create-result');

    if (!uid)     { tryShowResult(result, 'error', 'Please enter your name / ID.'); return; }
    if (!details) { tryShowResult(result, 'error', 'Please enter your details.'); return; }

    btn.disabled = true;
    spinner.classList.add('show');
    result.style.display = 'none';

    try {
        const resp = await fetch('/api/v2/create-resume', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ user_details_text: details, user_id: uid })
        });
        const data = await resp.json();

        if (resp.ok && data.success) {
            const name = filename || data.filename;
            tryShowResult(result, 'success',
                `✅ Resume generated! <a href="/api/pdfs/${data.filename}" target="_blank" style="color:inherit;font-weight:600;text-decoration:underline">Download PDF →</a>`);
            tryLoadPDFs();
        } else {
            tryShowResult(result, 'error', '❌ ' + (data.detail || data.message || 'Generation failed.'));
        }
    } catch (err) {
        tryShowResult(result, 'error', '❌ Network error: ' + err.message);
    } finally {
        btn.disabled = false;
        spinner.classList.remove('show');
    }
}

async function tryTailorResume() {
    const uid      = document.getElementById('tailor-uid').value.trim();
    const resume   = document.getElementById('tailor-resume').value.trim();
    const jd       = document.getElementById('tailor-jd').value.trim();
    const btn      = document.getElementById('tailor-btn');
    const spinner  = document.getElementById('tailor-spinner');
    const result   = document.getElementById('tailor-result');

    if (!uid)    { tryShowResult(result, 'error', 'Please enter your name / ID.'); return; }
    if (!resume) { tryShowResult(result, 'error', 'Please paste your current resume text.'); return; }
    if (!jd)     { tryShowResult(result, 'error', 'Please paste the job description.'); return; }

    btn.disabled = true;
    spinner.classList.add('show');
    result.style.display = 'none';

    try {
        const resp = await fetch('/api/v2/tailor-resume', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ resume_text: resume, jd_text: jd, user_id: uid })
        });
        const data = await resp.json();

        if (resp.ok && data.success) {
            tryShowResult(result, 'success',
                `✅ Tailored resume ready! <a href="/api/pdfs/${data.filename}" target="_blank" style="color:inherit;font-weight:600;text-decoration:underline">Download PDF →</a>`);
            tryLoadPDFs();
        } else {
            tryShowResult(result, 'error', '❌ ' + (data.detail || data.message || 'Tailoring failed.'));
        }
    } catch (err) {
        tryShowResult(result, 'error', '❌ Network error: ' + err.message);
    } finally {
        btn.disabled = false;
        spinner.classList.remove('show');
    }
}

async function tryLoadPDFs() {
    const list = document.getElementById('try-pdf-list');
    list.innerHTML = '<div class="empty-state"><div class="spin" style="margin:0 auto 12px"></div>Loading…</div>';

    try {
        const resp = await fetch('/api/pdfs');
        const data = await resp.json();

        if (data.success && data.pdfs && data.pdfs.length > 0) {
            list.innerHTML = data.pdfs.map(pdf => `
                <div class="pdf-row">
                    <div class="pdf-row-icon">📄</div>
                    <div class="pdf-row-info">
                        <div class="pdf-row-name">${pdf.filename}</div>
                        <div class="pdf-row-meta">${formatFileSize(pdf.size)} &middot; ${formatDate(pdf.modified)}</div>
                    </div>
                    <div class="pdf-row-btns">
                        <button class="pdf-icon-btn" title="Download" onclick="window.open('/api/pdfs/${pdf.filename}','_blank')">⬇️</button>
                        <button class="pdf-icon-btn del" title="Delete" onclick="tryDeletePDF('${pdf.filename}')">🗑️</button>
                    </div>
                </div>
            `).join('');
        } else {
            list.innerHTML = `
                <div class="empty-state">
                    <svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>
                    No PDFs yet — create your first resume above!
                </div>`;
        }
    } catch (err) {
        list.innerHTML = '<div class="empty-state">Failed to load PDFs. Check your connection.</div>';
    }
}

async function tryDeletePDF(filename) {
    if (!confirm(`Delete ${filename}?`)) return;
    try {
        const resp = await fetch(`/api/pdfs/${filename}`, { method: 'DELETE' });
        const data = await resp.json();
        if (data.success) tryLoadPDFs();
        else alert('Error deleting PDF: ' + (data.detail || data.message));
    } catch (err) {
        alert('Network error: ' + err.message);
    }
}

function tryShowResult(el, type, html) {
    el.className = 'try-result ' + type;
    el.innerHTML = html;
    el.style.display = '';
}
