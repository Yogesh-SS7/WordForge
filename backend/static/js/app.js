// ============================================================
//  WordForge — Frontend Logic (v2)
// ============================================================

(function () {
  'use strict';

  /* ── State ─────────────────────────────────────────────── */
  let allPasswords = [];
  let showingAll   = false;
  const PREVIEW_MAX = 50;   // rows shown by default before "show all"

  /* ── DOM Refs ──────────────────────────────────────────── */
  const form           = document.getElementById('osint-form');
  const generateBtn    = document.getElementById('btn-generate');
  const btnInnerText   = document.getElementById('btn-text');
  const spinner        = document.getElementById('btn-spinner');
  const errorBanner    = document.getElementById('error-banner');
  const errorMsg       = document.getElementById('error-message');
  const resultsSection = document.getElementById('results-section');
  const wordCount      = document.getElementById('word-count');
  const uniqueCount    = document.getElementById('unique-count');
  const minLenChip     = document.getElementById('min-len-chip');
  const minLenDisplay  = document.getElementById('min-len-display');
  const listContainer  = document.getElementById('wordlist-items');
  const showMoreWrap   = document.getElementById('show-more-wrap');
  const btnShowMore    = document.getElementById('btn-show-more');
  const btnDownload    = document.getElementById('btn-download');
  const btnCopy        = document.getElementById('btn-copy');
  const btnReset       = document.getElementById('btn-reset');
  const toast          = document.getElementById('toast');

  // Generation settings controls
  const countChips       = document.querySelectorAll('.count-chip');
  const customCountWrap  = document.getElementById('custom-count-wrap');
  const customCountInput = document.getElementById('custom-count');
  const lenMinus         = document.getElementById('len-minus');
  const lenPlus          = document.getElementById('len-plus');
  const lenDisplay       = document.getElementById('length-display');
  const lenInput         = document.getElementById('min-length');

  /* ── Generation Settings State ─────────────────────────── */
  let selectedCount = 100;  // default chip value

  /* ── Count Chip Logic ──────────────────────────────────── */
  countChips.forEach(chip => {
    chip.addEventListener('click', () => {
      countChips.forEach(c => c.classList.remove('active'));
      chip.classList.add('active');

      if (chip.dataset.count === 'custom') {
        customCountWrap.style.display = 'flex';
        selectedCount = parseInt(customCountInput.value, 10) || 200;
      } else {
        customCountWrap.style.display = 'none';
        selectedCount = parseInt(chip.dataset.count, 10);
      }
    });
  });

  customCountInput.addEventListener('input', () => {
    let v = parseInt(customCountInput.value, 10);
    if (isNaN(v) || v < 10)  v = 10;
    if (v > 1000) v = 1000;
    selectedCount = v;
  });

  /* ── Min Length Stepper ────────────────────────────────── */
  let minLen = 8;

  function updateLenDisplay() {
    lenDisplay.textContent = minLen;
    lenInput.value = minLen;
  }

  lenMinus.addEventListener('click', () => {
    if (minLen > 1) { minLen--; updateLenDisplay(); }
  });

  lenPlus.addEventListener('click', () => {
    if (minLen < 64) { minLen++; updateLenDisplay(); }
  });

  /* ── Toast Helper ──────────────────────────────────────── */
  let toastTimer = null;
  function showToast(msg, duration = 2500) {
    toast.textContent = msg;
    toast.classList.add('show');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => toast.classList.remove('show'), duration);
  }

  /* ── Error Banner ──────────────────────────────────────── */
  function showError(title, detail) {
    errorMsg.innerHTML = `<strong>⚠ ${title}</strong>${detail}`;
    errorBanner.classList.add('visible');
    errorBanner.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  }

  function hideError() {
    errorBanner.classList.remove('visible');
  }

  /* ── Form Validation ───────────────────────────────────── */
  function validateForm(data) {
    const nameField = document.getElementById('full-name');
    const name = data.full_name?.trim();
    if (!name) {
      nameField.classList.add('invalid');
      nameField.focus();
      showError('Validation Error', 'Full Name is required to generate a meaningful wordlist.');
      return false;
    }
    nameField.classList.remove('invalid');

    const extras = [
      data.dob, data.nicknames, data.partner_name, data.children_names,
      data.pet_names, data.workplace, data.hobbies, data.favorites,
      data.known_usernames, data.important_dates, data.context
    ].some(v => v && v.trim().length > 0);

    if (!extras) {
      showError('Insufficient Data', 'Please provide at least one additional field (DOB, nicknames, pets, workplace, etc.) for better results.');
      return false;
    }
    return true;
  }

  /* ── Collect Form Data ─────────────────────────────────── */
  function collectData() {
    return {
      full_name:       document.getElementById('full-name').value,
      nicknames:       document.getElementById('nicknames').value,
      dob:             document.getElementById('dob').value,
      partner_name:    document.getElementById('partner-name').value,
      children_names:  document.getElementById('children-names').value,
      pet_names:       document.getElementById('pet-names').value,
      workplace:       document.getElementById('workplace').value,
      hobbies:         document.getElementById('hobbies').value,
      favorites:       document.getElementById('favorites').value,
      known_usernames: document.getElementById('known-usernames').value,
      important_dates: document.getElementById('important-dates').value,
      context:         document.getElementById('context').value,
      // Generation controls
      count:           selectedCount,
      min_length:      minLen,
    };
  }

  /* ── Loading State ─────────────────────────────────────── */
  function setLoading(loading, count) {
    generateBtn.disabled = loading;
    if (loading) {
      generateBtn.classList.add('loading');
      btnInnerText.textContent = `Generating ${count} passwords...`;
      spinner.style.display = 'block';
    } else {
      generateBtn.classList.remove('loading');
      btnInnerText.textContent = '⚡ Generate Wordlist';
      spinner.style.display = 'none';
    }
  }

  /* ── Render Password List ──────────────────────────────── */
  function renderList(passwords, all) {
    listContainer.innerHTML = '';
    const items = all ? passwords : passwords.slice(0, PREVIEW_MAX);

    items.forEach((pw, i) => {
      const row = document.createElement('div');
      row.className = 'wordlist-item';
      // Stagger animation only for first 50 items to avoid lag
      row.style.animationDelay = `${Math.min(i * 8, 400)}ms`;
      row.innerHTML = `
        <span class="item-idx">${String(i + 1).padStart(3, '0')}</span>
        <span class="item-pw">${escapeHtml(pw)}</span>
        <button class="item-copy-btn" title="Copy" data-pw="${escapeHtml(pw)}">⎘</button>
      `;
      listContainer.appendChild(row);
    });

    // Row-level copy buttons
    listContainer.querySelectorAll('.item-copy-btn').forEach(btn => {
      btn.addEventListener('click', () => {
        navigator.clipboard.writeText(btn.dataset.pw)
          .then(() => showToast('✓ Copied to clipboard'));
      });
    });

    // Show/hide toggle
    if (passwords.length > PREVIEW_MAX) {
      showMoreWrap.style.display = 'block';
      btnShowMore.textContent = all
        ? `▲ Collapse (showing all ${passwords.length})`
        : `▼ Show All ${passwords.length} Passwords (${passwords.length - PREVIEW_MAX} more)`;
    } else {
      showMoreWrap.style.display = 'none';
    }
  }

  /* ── Escape HTML ───────────────────────────────────────── */
  function escapeHtml(str) {
    const d = document.createElement('div');
    d.textContent = str;
    return d.innerHTML;
  }

  /* ── Display Results ───────────────────────────────────── */
  function displayResults(data) {
    allPasswords = data.passwords;
    showingAll   = false;

    wordCount.textContent   = data.wordcount;
    uniqueCount.textContent = data.wordcount;

    // Show min-length badge
    if (data.min_length && data.min_length > 1) {
      minLenDisplay.textContent = data.min_length;
      minLenChip.style.display  = 'flex';
    } else {
      minLenChip.style.display  = 'none';
    }

    renderList(allPasswords, false);

    // Fade-in animation
    resultsSection.style.display = 'block';
    requestAnimationFrame(() => {
      requestAnimationFrame(() => resultsSection.classList.add('visible'));
    });
    resultsSection.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }

  /* ── Generate Handler ──────────────────────────────────── */
  async function handleGenerate(e) {
    e.preventDefault();
    hideError();

    const data = collectData();
    if (!validateForm(data)) return;

    setLoading(true, data.count);

    try {
      const response = await fetch('/api/generate', {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify(data),
      });

      const result = await response.json();

      if (!response.ok || !result.success) {
        const msg = result.error || 'Unknown server error.';
        if (msg.toLowerCase().includes('ollama') || msg.toLowerCase().includes('connect') || response.status === 503) {
          showError(
            'OLLAMA Service Not Detected',
            'Make sure OLLAMA is running on <code>localhost:11434</code> with the Dolphin model loaded.<br>Run: <code>ollama run dolphin-llama3:8b</code>'
          );
        } else if (msg.toLowerCase().includes('empty') || msg.toLowerCase().includes('no result')) {
          showError('No Results Returned', 'The AI returned an empty wordlist. Try adding more detailed OSINT data.');
        } else {
          showError('Generation Failed', msg);
        }
        return;
      }

      if (!result.passwords || result.passwords.length === 0) {
        showError('Empty Wordlist', 'AI returned no results. Try adjusting your input data.');
        return;
      }

      displayResults(result);

    } catch (err) {
      if (err.name === 'TypeError') {
        showError('Connection Error', 'Could not reach the WordForge backend. Make sure <code>python app.py</code> is running on port 5000.');
      } else {
        showError('Unexpected Error', err.message);
      }
    } finally {
      setLoading(false, data.count);
    }
  }

  /* ── Show More Toggle ──────────────────────────────────── */
  btnShowMore.addEventListener('click', () => {
    showingAll = !showingAll;
    renderList(allPasswords, showingAll);
  });

  /* ── Download ──────────────────────────────────────────── */
  btnDownload.addEventListener('click', () => {
    if (!allPasswords.length) return;
    const blob = new Blob([allPasswords.join('\n')], { type: 'text/plain' });
    const url  = URL.createObjectURL(blob);
    const a    = document.createElement('a');
    a.href     = url;
    a.download = `wordforge_${Date.now()}.txt`;
    a.click();
    URL.revokeObjectURL(url);
    showToast(`✓ Downloaded ${allPasswords.length} passwords`);
  });

  /* ── Copy All ──────────────────────────────────────────── */
  btnCopy.addEventListener('click', () => {
    if (!allPasswords.length) return;
    navigator.clipboard.writeText(allPasswords.join('\n'))
      .then(() => showToast(`✓ Copied ${allPasswords.length} passwords to clipboard`))
      .catch(() => showToast('✗ Clipboard access denied'));
  });

  /* ── Reset ─────────────────────────────────────────────── */
  btnReset.addEventListener('click', () => {
    allPasswords = [];
    showingAll   = false;
    resultsSection.classList.remove('visible');
    setTimeout(() => { resultsSection.style.display = 'none'; }, 400);
    listContainer.innerHTML = '';
    minLenChip.style.display = 'none';
    hideError();
    window.scrollTo({ top: 0, behavior: 'smooth' });
  });

  /* ── Clear validation on input ─────────────────────────── */
  document.getElementById('full-name').addEventListener('input', function () {
    this.classList.remove('invalid');
    hideError();
  });

  /* ── Bind Submit (form only — button is type=submit, no duplicate listener needed) */
  form.addEventListener('submit', handleGenerate);

})();
