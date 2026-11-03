(() => {
  const make = document.getElementById('v_make');
  const model = document.getElementById('v_model');
  const options = document.getElementById('vehicle-model-options');
  const catalog = JSON.parse(document.getElementById('vehicle-catalog').textContent);
  const normalize = value => value.trim().toLocaleLowerCase('ru');
  const find = value => catalog.find(entry =>
    [entry.make, ...entry.aliases].some(name => normalize(name) === normalize(value)));
  let committedMake = normalize(make.value);
  let committedEntry = find(make.value);

  function updateModels() {
    const entry = find(make.value);
    options.replaceChildren(...(entry?.models || []).map(name => {
      const option = document.createElement('option');
      option.value = name;
      return option;
    }));
  }
  make.addEventListener('input', updateModels);
  make.addEventListener('change', () => {
    const entry = find(make.value);
    // A spelling/alias change for the same make must not erase the model.
    if (normalize(make.value) !== committedMake && (!entry || entry !== committedEntry)) {
      model.value = '';
    }
    committedMake = normalize(make.value);
    committedEntry = entry;
    updateModels();
  });
  updateModels();
})();
