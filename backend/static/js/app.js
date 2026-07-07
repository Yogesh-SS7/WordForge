// ============================================================
//  WordForge — Frontend Logic (v3)
//  Changes: Min + Max length steppers, quick-preset chips,
//           max_length sent in POST, results badge updated.
// ============================================================

(function () {
  'use strict';

  /* ── State ─────────────────────────────────────────────── */
  let allPasswords = [];
  let showingAll   = false;
  const PREVIEW_MAX = 50;

  /* ── Generation settings state ─────────────────────────── */
  let selectedCount = 100;
  let minLen        = 12;   // default — matches preset-12 active
  let maxLen        = 12;   // same as minLen → exact mode

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
  const minLenLabel    = document.getElementById('min-len-label');
  const listContainer  = document.getElementById('wordlist-items');
  const showMoreWrap   = document.getElementById('show-more-wrap');
  const btnShowMore    = document.getElementById('btn-show-more');
  const btnDownload    = document.getElementById('btn-download');
  const btnCopy        = document.getElementById('btn-copy');
  const btnReset       = document.getElementById('btn-reset');
  const toast          = document.getElementById('toast');

  // Count chips
  const countChips       = document.querySelectorAll('.count-chip');
  const customCountWrap  = document.getElementById('custom-count-wrap');
  const customCountInput = document.getElementById('custom-count');

  // Length controls
  const lenMinMinus      = document.getElementById('len-min-minus');
  const lenMinPlus       = document.getElementById('len-min-plus');
  const lenMaxMinus      = document.getElementById('len-max-minus');
  const lenMaxPlus       = document.getElementById('len-max-plus');
  const minLenDisplayEl  = document.getElementById('min-length-display');
  const maxLenDisplayEl  = document.getElementById('max-length-display');
  const minLenInput      = document.getElementById('min-length');
  const maxLenInput      = document.getElementById('max-length');
  const lengthHint       = document.getElementById('length-hint');
  const lengthPresets    = document.querySelectorAll('.length-preset');

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

  /* ── Length Helpers ────────────────────────────────────── */
  function syncLengthUI() {
    // Update display spans
    minLenDisplayEl.textContent = minLen;
    maxLenDisplayEl.textContent = maxLen;

    // Update hidden inputs (sent in POST body)
    minLenInput.value = minLen;
    maxLenInput.value = maxLen;

    // Update hint text
    if (minLen === maxLen) {
      lengthHint.textContent = `exact ${minLen} characters per password`;
    } else {
      lengthHint.textContent = `between ${minLen} and ${maxLen} characters per password`;
    }

    // Clear preset highlight if values no longer match any preset
    const matchedPreset = [...lengthPresets].find(
      b => parseInt(b.dataset.len, 10) === minLen && minLen === maxLen
    );
    lengthPresets.forEach(b => b.classList.remove('active'));
    if (matchedPreset) matchedPreset.classList.add('active');
  }

  /* ── Min stepper ───────────────────────────────────────── */
  lenMinMinus.addEventListener('click', () => {
    if (minLen > 1) {
      minLen--;
      // If minLen goes below maxLen in exact mode, allow range; else just decrement
      if (minLen > maxLen) maxLen = minLen;
      syncLengthUI();
    }
  });

  lenMinPlus.addEventListener('click', () => {
    if (minLen < 64) {
      minLen++;
      // Min can't exceed max — push max up with it
      if (minLen > maxLen) maxLen = minLen;
      syncLengthUI();
    }
  });

  /* ── Max stepper ───────────────────────────────────────── */
  lenMaxMinus.addEventListener('click', () => {
    if (maxLen > minLen) {
      maxLen--;
      syncLengthUI();
    }
    // If maxLen == minLen, it becomes exact mode — that's fine, do nothing extra
  });

  lenMaxPlus.addEventListener('click', () => {
    if (maxLen < 64) {
      maxLen++;
      syncLengthUI();
    }
  });

  /* ── Preset buttons ────────────────────────────────────── */
  lengthPresets.forEach(btn => {
    btn.addEventListener('click', () => {
      const n = parseInt(btn.dataset.len, 10);
      minLen = maxLen = n;   // exact mode
      syncLengthUI();
    });
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
      max_length:      maxLen,   // new — hard ceiling enforced by backend
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
      row.style.animationDelay = `${Math.min(i * 8, 400)}ms`;
      row.innerHTML = `
        <span class="item-idx">${String(i + 1).padStart(3, '0')}</span>
        <span class="item-pw">${escapeHtml(pw)}</span>
        <button class="item-copy-btn" title="Copy" data-pw="${escapeHtml(pw)}">⎘</button>
      `;
      listContainer.appendChild(row);
    });

    listContainer.querySelectorAll('.item-copy-btn').forEach(btn => {
      btn.addEventListener('click', () => {
        navigator.clipboard.writeText(btn.dataset.pw)
          .then(() => showToast('✓ Copied to clipboard'));
      });
    });

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

    // Update the length badge
    const rMin = data.min_length;
    const rMax = data.max_length ?? rMin;   // backwards compat: old server may not send max_length

    if (rMin && rMin > 1) {
      if (rMin === rMax) {
        minLenDisplay.textContent = rMin;
        minLenLabel.textContent   = 'char fixed';
      } else {
        minLenDisplay.textContent = `${rMin}–${rMax}`;
        minLenLabel.textContent   = 'char range';
      }
      minLenChip.style.display = 'flex';
    } else {
      minLenChip.style.display = 'none';
    }

    renderList(allPasswords, false);

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

  /* ── Bind Submit ───────────────────────────────────────── */
  form.addEventListener('submit', handleGenerate);

  /* ── Init ──────────────────────────────────────────────── */
  syncLengthUI();   // render initial state (preset 12 active)

})();
