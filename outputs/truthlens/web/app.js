'use strict';
const $ = id => document.getElementById(id);
let kind = 'image', selectedFile = null, objectURL = null, activeReport = null, busy = false;
let historyRefresh = null;
const esc = value => String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
async function api(path, options) {
  const response = await fetch(path, options);
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.error || 'The request could not be completed.');
  return payload;
}
function showError(message) { $('error-message').textContent = message; $('error-message').hidden = false; }
function setView(view) {
  document.querySelectorAll('.view').forEach(el => el.hidden = el.id !== `view-${view}`);
  document.querySelectorAll('.nav').forEach(el => el.classList.toggle('active', el.dataset.view === view));
  if (view === 'history') loadHistory();
}
document.querySelectorAll('.nav').forEach(button => button.addEventListener('click', () => setView(button.dataset.view)));
function clearFile() {
  selectedFile = null;
  $('file-input').value = '';
  $('file-preview').hidden = true;
  $('preview-video').pause();
  $('preview-video').removeAttribute('src');
  $('preview-image').removeAttribute('src');
  if (objectURL) URL.revokeObjectURL(objectURL);
  objectURL = null;
}
function setKind(next) {
  if (busy) return;
  kind = next;
  clearFile();
  $('error-message').hidden = true;
  document.querySelectorAll('.tab').forEach(el => { el.classList.toggle('active', el.dataset.kind === kind); el.setAttribute('aria-selected', String(el.dataset.kind === kind)); el.tabIndex = el.dataset.kind === kind ? 0 : -1; });
  $('media-input').hidden = kind === 'news';
  $('news-input').hidden = kind !== 'news';
  $('file-input').accept = kind === 'image' ? 'image/jpeg,image/png,image/webp' : '.mp4,.mov,.webm,.avi';
  $('drop-title').textContent = kind === 'image' ? 'Drop an image to investigate' : 'Drop a video to investigate';
  $('file-help').textContent = kind === 'image' ? 'JPEG, PNG, WebP · up to 50 MB · 20 megapixels' : 'MP4, MOV, WebM, AVI · 50 MB · 2 minutes · up to 4K';
}
const tabs = [...document.querySelectorAll('.tab')];
tabs.forEach((button, index) => {
  button.addEventListener('click', () => setKind(button.dataset.kind));
  button.addEventListener('keydown', event => {
    if (busy) return;
    const directions = {ArrowRight: 1, ArrowLeft: -1};
    if (event.key in directions) { event.preventDefault(); const next = tabs[(index + directions[event.key] + tabs.length) % tabs.length]; setKind(next.dataset.kind); next.focus(); }
    if (event.key === 'Home' || event.key === 'End') { event.preventDefault(); const next = tabs[event.key === 'Home' ? 0 : tabs.length - 1]; setKind(next.dataset.kind); next.focus(); }
  });
});
function chooseFile(file) {
  if (busy || !file) return;
  $('error-message').hidden = true;
  if (file.size === 0 || file.size > 50 * 1024 * 1024) return showError('Choose a nonempty file up to 50 MB.');
  const valid = kind === 'image' ? /\.(jpe?g|png|webp)$/i : /\.(mp4|mov|webm|avi)$/i;
  if (!valid.test(file.name)) return showError(`Choose a supported ${kind} file.`);
  clearFile();
  selectedFile = file;
  objectURL = URL.createObjectURL(file);
  $('file-name').textContent = file.name;
  $('file-size').textContent = `${(file.size / 1024 / 1024).toFixed(2)} MB`;
  $('preview-image').hidden = kind !== 'image';
  $('preview-video').hidden = kind !== 'video';
  $(kind === 'image' ? 'preview-image' : 'preview-video').src = objectURL;
  $('file-preview').hidden = false;
}
$('file-input').addEventListener('change', e => chooseFile(e.target.files[0]));
$('remove-file').addEventListener('click', () => { if (!busy) clearFile(); });
['dragenter', 'dragover'].forEach(type => $('dropzone').addEventListener(type, e => { e.preventDefault(); $('dropzone').classList.add('dragging'); }));
['dragleave', 'drop'].forEach(type => $('dropzone').addEventListener(type, e => { e.preventDefault(); $('dropzone').classList.remove('dragging'); if (type === 'drop') chooseFile(e.dataTransfer.files[0]); }));
$('article-text').addEventListener('input', () => $('character-count').textContent = `${$('article-text').value.length.toLocaleString()} / 50,000`);
$('sample-news').addEventListener('click', () => {
  $('article-title').value = 'Sample: a claim that needs evidence';
  $('article-text').value = 'SHOCKING! A new miracle device is guaranteed to cut every household’s electricity bill by 90%. Everyone knows the power companies do not want this secret revealed. According to an unnamed expert, the device works in all homes. The article provides no study, manufacturer details, or independently measured results. Buy it today!';
  $('article-text').dispatchEvent(new Event('input'));
});
function setBusy(value) {
  busy = value;
  $('analyze-button').disabled = value;
  $('analyze-button').textContent = value ? 'Analysis in progress…' : 'Analyze content';
  $('progress').hidden = !value;
  document.querySelectorAll('#analysis-form input, #analysis-form textarea, #analysis-form button:not(#analyze-button), .tab').forEach(el => el.disabled = value);
}
$('analysis-form').addEventListener('submit', async event => {
  event.preventDefault();
  if (busy) return;
  $('error-message').hidden = true;
  const submittedKind = kind;
  let body, name, contentType;
  if (kind === 'news') {
    const text = $('article-text').value.trim();
    if (text.length < 40) return showError('Paste at least 40 characters of article text.');
    body = JSON.stringify({text, source_url: $('source-url').value});
    name = $('article-title').value.trim() || 'Untitled article';
    contentType = 'application/json';
  } else {
    if (!selectedFile) return showError(`Select a ${kind} file first.`);
    body = selectedFile; name = selectedFile.name; contentType = 'application/octet-stream';
  }
  setBusy(true);
  try {
    const job = await api(`/api/analyze/${submittedKind}?name=${encodeURIComponent(name)}`, {method:'POST',headers:{'Content-Type':contentType},body});
    let completed = false;
    for (let attempt = 0; attempt < 180; attempt++) {
      await new Promise(resolve => setTimeout(resolve, 1000));
      const result = await api(`/api/reports/${job.id}`);
      if (result.status === 'failed') throw new Error(result.report.error);
      if (result.status === 'complete') { renderReport(result.report); completed = true; break; }
    }
    if (!completed) throw new Error('Analysis is still running. Check report history shortly.');
    await loadHistory();
  } catch (error) { showError(error.message); }
  finally { setBusy(false); }
});
function renderReport(report) {
  activeReport = report;
  const findings = report.findings.map(f => `<div class="finding"><span class="finding-badge ${f.status === 'review' ? 'review' : ''}">${esc(f.status)}</span><div><h3>${esc(f.name)}</h3><p>${esc(f.detail)}</p></div>${f.value != null ? `<span class="finding-value">${esc(f.value)}</span>` : ''}</div>`).join('');
  const model = report.model ? `<div class="model-result"><h3>${esc(report.model.name)}</h3><p>Manipulated-class model output: <strong>${(report.model.manipulated_probability * 100).toFixed(1)}%</strong> · ${esc(report.model.method)}</p><p>${esc(report.model.scope)}. ${esc(report.model.note)}</p></div>` : '';
  $('report').innerHTML = `<div class="report-header"><div><div class="eyebrow">EVIDENCE REPORT / ${esc(report.kind.toUpperCase())}</div><h2 class="report-title">${esc(report.name)}</h2></div><div class="report-actions"><button class="secondary" id="download-report">Export JSON</button><button class="secondary" id="print-report">Print / PDF</button></div></div><span class="verdict">${esc(report.verdict)}</span><p class="report-summary">${esc(report.summary)}</p><div class="findings">${findings}</div>${model}<div class="report-columns"><div><h3>What remains unknown</h3><ul>${report.limitations.map(item => `<li>${esc(item)}</li>`).join('')}</ul></div><div><h3>Recommended next steps</h3><ul>${report.next_steps.map(item => `<li>${esc(item)}</li>`).join('')}</ul></div></div><details><summary>Technical measurements</summary><pre>${esc(JSON.stringify(report.metrics, null, 2))}</pre></details><div class="hash">SHA-256: ${esc(report.sha256)}<br>Report ${esc(report.id)} · ${esc(new Date(report.created_at).toLocaleString())}</div>`;
  $('download-report').addEventListener('click', () => {
    const link = document.createElement('a'); link.href = `/api/reports/${activeReport.id}/download`; link.download = `truthlens-${activeReport.id}.json`; document.body.appendChild(link); link.click(); link.remove();
  });
  $('print-report').addEventListener('click', () => window.print());
  $('report').hidden = false;
  $('report').scrollIntoView({behavior:matchMedia('(prefers-reduced-motion: reduce)').matches ? 'instant' : 'smooth',block:'start'});
}
async function loadHistory() {
  try {
    const reports = await api('/api/reports');
    $('history-count').textContent = reports.length;
    $('history-list').innerHTML = reports.length ? reports.map(report => `<div class="history-row"><span class="kind-tag">${esc(report.kind)}</span><div class="history-info"><strong>${esc(report.name)}</strong><p>${esc(new Date(report.created * 1000).toLocaleString())} · ${esc(report.status)}</p></div><button class="secondary" data-open="${esc(report.id)}">${report.status === 'complete' ? 'View report' : 'View status'}</button><button class="secondary" data-delete="${esc(report.id)}" ${['queued','processing'].includes(report.status) ? 'disabled' : ''}>Delete</button></div>`).join('') : '<div class="empty"><h2>No investigations yet</h2><p>Your first report will appear here after you analyze content.</p><button class="secondary" id="empty-analyze">Start an analysis</button></div>';
    if ($('empty-analyze')) $('empty-analyze').addEventListener('click', () => setView('analyze'));
    document.querySelectorAll('[data-open]').forEach(button => button.addEventListener('click', async () => {
      try { const result = await api(`/api/reports/${button.dataset.open}`); setView('analyze'); if (result.status === 'complete') renderReport(result.report); else showError(result.status === 'failed' ? result.report.error : `This investigation is ${result.status}. Check history shortly.`); } catch (e) { showError(e.message); }
    }));
    document.querySelectorAll('[data-delete]').forEach(button => button.addEventListener('click', async () => {
      if (!confirm('Permanently delete this local report?')) return;
      try { await api(`/api/reports/${button.dataset.delete}`, {method:'DELETE',headers:{'X-TruthLens-Action':'delete'}}); if (activeReport?.id === button.dataset.delete) { activeReport = null; $('report').hidden = true; } await loadHistory(); } catch (e) { alert(e.message); }
    }));
    clearTimeout(historyRefresh);
    if (reports.some(report => ['queued','processing'].includes(report.status))) historyRefresh = setTimeout(loadHistory, 3000);
  } catch (error) { $('history-list').textContent = error.message; }
}
async function init() {
  setKind('image');
  try {
    const health = await api('/api/health');
    $('server-status').textContent = 'Local engine connected'; $('server-status').classList.add('online');
    $('model-status').textContent = health.onnx_configured ? 'ONNX configuration supplied' : health.feature_model ? 'Local feature model available' : 'Forensic mode · no trained model';
    $('model-description').textContent = health.onnx_configured || health.feature_model ? 'Model validity is checked when analyzing an image.' : 'Measurements and language cues are active. Model predictions require trained weights.';
    if (!health.video) $('model-description').textContent += ' Install OpenCV to enable video decoding.';
    await loadHistory();
  } catch (error) { $('server-status').textContent = 'Engine unavailable'; showError('Could not reach the local analysis engine. Start server.py and reload.'); }
}
init();
