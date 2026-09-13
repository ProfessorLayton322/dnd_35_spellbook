(() => {
  document.addEventListener('submit', (submitEvent) => {
    const message = submitEvent.target.dataset.confirm;
    if (message && !window.confirm(message)) submitEvent.preventDefault();
  });

  const flashes = document.querySelector('#flash-messages');

  const setFlash = (message, className) => {
    const item = document.createElement('p');
    item.className = className;
    item.textContent = message;
    flashes.replaceChildren(item);
  };

  // Posts a form to its data-progress-action, passes every NDJSON progress
  // event to onEvent, and resolves with the final done event.
  const streamProgress = async (form, onEvent) => {
    const response = await fetch(form.dataset.progressAction, {
      method: 'POST',
      body: new FormData(form),
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
        onEvent(event);
        if (event.type === 'done') doneEvent = event;
      }
      if (done) break;
    }
    if (!doneEvent) throw new Error('The import ended before it was saved.');
    return doneEvent;
  };

  const importForm = document.querySelector('#class-import-form');
  const progressPanel = document.querySelector('#class-import-progress');

  if (importForm && progressPanel && window.ReadableStream) {
    const button = importForm.querySelector('button');
    const title = document.querySelector('#import-progress-title');
    const percent = document.querySelector('#import-progress-percent');
    const bar = document.querySelector('#import-progress-bar');
    const levels = document.querySelector('#completed-levels');
    const spells = document.querySelector('#downloaded-spells');
    const skipped = document.querySelector('#skipped-spells');
    const status = document.querySelector('#import-progress-status');

    const updateProgress = (event) => {
      const completedLevels = event.completed_levels ?? 0;
      const totalLevels = event.total_levels ?? 0;
      const downloadedSpells = event.downloaded_spells ?? 0;
      const skippedSpells = event.skipped_spells ?? 0;
      const processedSpells = event.processed_spells ?? (downloadedSpells + skippedSpells);
      const totalSpells = event.total_spells;
      levels.textContent = `${completedLevels} / ${totalLevels || '—'}`;
      spells.textContent = `${downloadedSpells} / ${totalSpells ?? '—'}`;
      skipped.textContent = String(skippedSpells);

      if (totalSpells > 0) {
        const ratio = Math.max(0, Math.min(1, processedSpells / totalSpells));
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
      if (event.type === 'level_scanned') {
        status.textContent = event.spells_in_level
          ? `Level ${event.level}: found ${event.spells_in_level} spells.`
          : `Level ${event.level}: no spells listed; skipping this level.`;
      }
      if (event.type === 'download_started') status.textContent = `Spell lists scanned. Downloading ${totalSpells} spells…`;
      if (event.type === 'spell_downloaded') status.textContent = `Level ${event.level}: downloaded ${event.spell_name}.`;
      if (event.type === 'spell_skipped') status.textContent = `Level ${event.level}: skipped missing spell ${event.spell_name} (404).`;
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
      skipped.textContent = '0';
      status.textContent = 'Connecting to the spell database…';
      flashes.replaceChildren();

      try {
        const doneEvent = await streamProgress(importForm, updateProgress);
        bar.value = 1;
        bar.textContent = '100%';
        percent.textContent = '100%';
        title.textContent = `${doneEvent.class_name} imported`;
        const skippedSummary = doneEvent.skipped_spells ? `; ${doneEvent.skipped_spells} missing skipped` : '';
        status.textContent = `${doneEvent.unique_spells} unique spells saved${skippedSummary}. Refreshing…`;
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

  const summonForm = document.querySelector('#summon-import-form');
  const summonPanel = document.querySelector('#summon-import-progress');

  if (summonForm && summonPanel && window.ReadableStream) {
    const button = summonForm.querySelector('button');
    const title = document.querySelector('#summon-progress-title');
    const percent = document.querySelector('#summon-progress-percent');
    const bar = document.querySelector('#summon-progress-bar');
    const tables = document.querySelector('#summon-tables');
    const creaturePages = document.querySelector('#summon-creature-pages');
    const status = document.querySelector('#summon-progress-status');

    const updateProgress = (event) => {
      const parsedTables = event.parsed_tables ?? 0;
      const totalTables = event.total_tables ?? 0;
      const downloadedPages = event.downloaded_monster_pages ?? 0;
      const totalPages = event.total_monster_pages;
      tables.textContent = `${parsedTables} / ${totalTables || '—'}`;
      creaturePages.textContent = `${downloadedPages} / ${totalPages ?? '—'}`;

      // The creature page count is only known once every summon table is read.
      if (totalPages != null) {
        const ratio = Math.max(0, Math.min(1, (parsedTables + downloadedPages) / ((totalTables + totalPages) || 1)));
        bar.value = ratio;
        bar.textContent = `${Math.round(ratio * 100)}%`;
        percent.textContent = `${Math.round(ratio * 100)}%`;
      } else {
        bar.removeAttribute('value');
        bar.textContent = 'Reading summon tables';
        percent.textContent = 'Reading…';
      }

      if (event.type === 'summon_table_parsed') status.textContent = `Read ${event.spell_name}: ${event.entries} creatures.`;
      if (event.type === 'monster_download_started') status.textContent = `Summon tables read. Downloading ${totalPages} creature pages…`;
      if (event.type === 'monster_page_downloaded') status.textContent = `Downloaded ${event.monster_name}.`;
    };

    summonForm.addEventListener('submit', async (submitEvent) => {
      submitEvent.preventDefault();
      button.disabled = true;
      summonPanel.hidden = false;
      summonPanel.classList.remove('complete', 'failed');
      title.textContent = 'Importing summons';
      percent.textContent = '0%';
      bar.value = 0;
      tables.textContent = '0 / —';
      creaturePages.textContent = '0 / —';
      status.textContent = 'Connecting to d20srd.org…';
      flashes.replaceChildren();

      try {
        const doneEvent = await streamProgress(summonForm, updateProgress);
        bar.value = 1;
        bar.textContent = '100%';
        percent.textContent = '100%';
        if (doneEvent.unresolved) {
          title.textContent = 'Summons imported with problems';
          status.textContent = `${doneEvent.unresolved} summon entries have no matching statblock. Refreshing…`;
          summonPanel.classList.add('failed');
        } else {
          title.textContent = 'Summons imported';
          status.textContent = `${doneEvent.statblocks} creature statblocks saved. Refreshing…`;
          summonPanel.classList.add('complete');
        }
        window.setTimeout(() => window.location.assign(doneEvent.redirect), 700);
      } catch (error) {
        const message = error instanceof Error ? error.message : String(error);
        summonPanel.classList.add('failed');
        title.textContent = 'Summon import failed';
        status.textContent = message;
        setFlash(message, 'error');
        button.disabled = false;
      }
    });
  }

  const query = document.querySelector('#spell-query');
  const results = document.querySelector('#spell-results');
  if (!query || !results) return;
  const form = query.closest('form');
  let serial = 0;
  let active = -1;
  const choices = () => [...results.querySelectorAll('button')];
  const setActive = (index, scroll) => {
    const buttons = choices();
    active = buttons.length ? (index + buttons.length) % buttons.length : -1;
    buttons.forEach((button, position) => button.classList.toggle('active', position === active));
    if (scroll && active >= 0) buttons[active].scrollIntoView({ block: 'nearest' });
  };

  // Each suggestion is a submit button carrying its spell_id, so a tap or click adds it.
  query.addEventListener('input', async () => {
    const current = ++serial;
    if (!query.value.trim()) {
      results.replaceChildren();
      active = -1;
      return;
    }
    const response = await fetch('/api/spells?prefix=' + encodeURIComponent(query.value));
    const values = await response.json();
    if (current !== serial) return;
    const items = values.map((value) => {
      const item = document.createElement('li');
      const button = document.createElement('button');
      button.name = 'spell_id';
      button.value = value.id;
      button.textContent = value.name;
      item.append(button);
      return item;
    });
    if (!items.length) {
      const empty = document.createElement('li');
      empty.className = 'muted';
      empty.textContent = 'No spells start with that.';
      items.push(empty);
    }
    results.replaceChildren(...items);
    setActive(0, false);
  });

  query.addEventListener('keydown', (keyEvent) => {
    if (keyEvent.key === 'ArrowDown' || keyEvent.key === 'ArrowUp') {
      keyEvent.preventDefault();
      setActive(active + (keyEvent.key === 'ArrowDown' ? 1 : -1), true);
    } else if (keyEvent.key === 'Enter') {
      keyEvent.preventDefault();
      const button = choices()[active];
      if (button) form.requestSubmit(button);
    }
  });

  // Never submit without a chosen spell, e.g. on Enter before suggestions arrive.
  form.addEventListener('submit', (submitEvent) => {
    if (!submitEvent.submitter || !submitEvent.submitter.value) submitEvent.preventDefault();
  });
})();
