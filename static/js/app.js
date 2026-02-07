// Initialize CodeMirror editor
let editor;
let currentPDFs = [];

// Initialize the application
document.addEventListener('DOMContentLoaded', () => {
    initializeEditor();
    checkHealth();
    loadPDFList();
    setupEventListeners();
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
