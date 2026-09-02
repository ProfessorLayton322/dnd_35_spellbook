(() => {
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
