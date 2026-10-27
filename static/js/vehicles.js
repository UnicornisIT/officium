(() => {
  const kind = document.getElementById('v_kind');
  const body = document.getElementById('v_body_type');
  const color = document.getElementById('v_color');
  const preview = document.getElementById('vehiclePreview');
  const previewBody = document.getElementById('vehiclePreviewBody');
  const picker = document.querySelector('.vehicle-color-picker');
  const swatches = picker.querySelectorAll('[data-vehicle-color]');
  const eyedropper = picker.querySelector('.vehicle-eyedropper');
  const colorStatus = picker.querySelector('.vehicle-color-status');
  const updatePreview = () => {
    preview.style.color = color.value;
    picker.style.setProperty('--vehicle-color', color.value);
    swatches.forEach(button => button.setAttribute('aria-pressed', String(button.dataset.vehicleColor === color.value.toLowerCase())));
    previewBody.setAttribute('href', `${previewBody.dataset.baseUrl}${body.value}.svg?v=detail-2#body`);
  };
  const sync = () => {
    const trailer = kind.value === 'trailer';
    document.getElementById('v_fuel_type').closest('.vehicle-field').hidden = trailer;
    document.querySelectorAll('[data-trailer]').forEach(el => { el.hidden = !trailer; });
    document.querySelectorAll('[data-powered]').forEach(el => { el.hidden = trailer; });
    document.querySelectorAll('[data-tractor]').forEach(el => { el.hidden = kind.value !== 'tractor_unit'; });
    if (trailer) {
      document.getElementById('v_fuel_type').value = 'none';
      document.getElementById('v_body_type').value = 'trailer';
    }
    if (kind.value === 'tractor_unit') document.getElementById('v_body_type').value = 'tractor_unit';
    if (!trailer) document.getElementById('v_tractor').value = '';
    updatePreview();
  };
  kind.addEventListener('change', sync);
  body.addEventListener('change', updatePreview);
  color.addEventListener('input', updatePreview);
  color.addEventListener('change', updatePreview);
  swatches.forEach(button => button.addEventListener('click', () => {
    color.value = button.dataset.vehicleColor;
    colorStatus.textContent = '';
    updatePreview();
  }));
  if (typeof window.EyeDropper === 'function') {
    eyedropper.hidden = false;
    eyedropper.addEventListener('click', async () => {
      colorStatus.textContent = '';
      try {
        const result = await new window.EyeDropper().open();
        color.value = result.sRGBHex;
        updatePreview();
      } catch (error) {
        if (error.name !== 'AbortError') colorStatus.textContent = 'Не удалось выбрать цвет. Воспользуйтесь палитрой.';
      }
    });
  }
  picker.addEventListener('keydown', event => {
    if (event.key === 'Escape') {
      picker.open = false;
      picker.querySelector('summary').focus();
    }
  });
  sync();
})();
