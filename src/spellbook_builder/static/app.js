(() => {
  const importForm = document.querySelector('#class-import-form');
  const progressPanel = document.querySelector('#class-import-progress');

  if (importForm && progressPanel && window.ReadableStream) {
    const button = importForm.querySelector('button');
    const title = document.querySelector('#import-progress-title');
    const percent = document.querySelector('#import-progress-percent');
    const bar = document.querySelector('#import-progress-bar');
    const levels = document.querySelector('#completed-levels');
    const spells = document.querySelector('#downloaded-spells');
    const status = document.querySelector('#import-progress-status');
    const flashes = document.querySelector('#flash-messages');

    const setFlash = (message, className) => {
      const item = document.createElement('p');
      item.className = className;
      item.textContent = message;
      flashes.replaceChildren(item);
    };

    const updateProgress = (event) => {
      const completedLevels = event.completed_levels ?? 0;
      const totalLevels = event.total_levels ?? 0;
      const downloadedSpells = event.downloaded_spells ?? 0;
      const totalSpells = event.total_spells;
      levels.textContent = `${completedLevels} / ${totalLevels || '—'}`;
      spells.textContent = `${downloadedSpells} / ${totalSpells ?? '—'}`;

      if (totalSpells > 0) {
        const ratio = Math.max(0, Math.min(1, downloadedSpells / totalSpells));
        bar.value = ratio;
        bar.textContent = `${Math.round(ratio * 100)}%`;
        percent.textContent = `${Math.round(ratio * 100)}%`;
      } else {
        bar.removeAttribute('value');
        bar.textContent = 'Scanning spell lists';
        percent.textContent = 'Scanning…';
      }

      if (event.class_name) title.textContent = `Importing ${event.class_name}`;
      if (event.type === 'class_discovered') status.textContent = `Found ${totalLevels} spell levels. Scanning their spell lists…`;
      if (event.type === 'level_scan_started') status.textContent = `Scanning the level ${event.level} spell list…`;
      if (event.type === 'level_scanned') status.textContent = `Level ${event.level}: found ${event.spells_in_level} spells.`;
      if (event.type === 'download_started') status.textContent = `Spell lists scanned. Downloading ${totalSpells} spells…`;
      if (event.type === 'spell_downloaded') status.textContent = `Level ${event.level}: downloaded ${event.spell_name}.`;
      if (event.type === 'level_completed') status.textContent = `Completed level ${event.level} (${event.spells_in_level} spells).`;
    };

    importForm.addEventListener('submit', async (submitEvent) => {
      submitEvent.preventDefault();
      button.disabled = true;
      progressPanel.hidden = false;
      progressPanel.classList.remove('complete', 'failed');
      title.textContent = 'Importing class';
      percent.textContent = '0%';
      bar.value = 0;
      levels.textContent = '0 / —';
      spells.textContent = '0 / —';
      status.textContent = 'Connecting to the spell database…';
      flashes.replaceChildren();

      try {
        const response = await fetch(importForm.dataset.progressAction, {
          method: 'POST',
          body: new FormData(importForm),
          headers: { Accept: 'application/x-ndjson' },
        });
        if (!response.ok) {
          const failure = await response.json().catch(() => ({}));
          throw new Error(failure.error || `Import failed (${response.status})`);
        }
        if (!response.body) throw new Error('This browser cannot read import progress.');

        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffered = '';
        let doneEvent = null;
        while (true) {
          const { value, done } = await reader.read();
          buffered += decoder.decode(value || new Uint8Array(), { stream: !done });
          const lines = buffered.split('\n');
          buffered = done ? '' : lines.pop();
          for (const line of lines) {
            if (!line.trim()) continue;
            const event = JSON.parse(line);
            if (event.type === 'error') throw new Error(event.message || 'Import failed');
            updateProgress(event);
            if (event.type === 'done') doneEvent = event;
          }
          if (done) break;
        }
        if (!doneEvent) throw new Error('The import ended before it was saved.');

        bar.value = 1;
        bar.textContent = '100%';
        percent.textContent = '100%';
        title.textContent = `${doneEvent.class_name} imported`;
        status.textContent = `${doneEvent.unique_spells} unique spells saved. Refreshing…`;
        progressPanel.classList.add('complete');
        window.setTimeout(() => window.location.assign(doneEvent.redirect), 700);
      } catch (error) {
        const message = error instanceof Error ? error.message : String(error);
        progressPanel.classList.add('failed');
        title.textContent = 'Import failed';
        status.textContent = message;
        setFlash(message, 'error');
        button.disabled = false;
      }
    });
  }

  const query = document.querySelector('#spell-query');
  const results = document.querySelector('#spell-results');
  const selected = document.querySelector('#spell-id');
  if (!query || !results || !selected) return;
  let serial = 0;
  query.addEventListener('input', async () => {
    const current = ++serial;
    selected.value = '';
    results.replaceChildren();
    if (!query.value.trim()) return;
    const response = await fetch('/api/spells?prefix=' + encodeURIComponent(query.value));
    const values = await response.json();
    if (current !== serial) return;
    for (const value of values) {
      const option = document.createElement('option');
      option.value = value.id;
      option.textContent = value.name;
      results.append(option);
    }
  });
  results.addEventListener('change', () => { selected.value = results.value; });
  results.addEventListener('dblclick', () => { selected.value = results.value; results.closest('form').requestSubmit(); });
})();
