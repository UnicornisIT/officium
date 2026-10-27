(() => {
  const kind = document.getElementById('v_kind');
  const body = document.getElementById('v_body_type');
  const color = document.getElementById('v_color');
  const preview = document.getElementById('vehiclePreview');
  const previewBody = document.getElementById('vehiclePreviewBody');
  const updatePreview = () => {
    preview.style.color = color.value;
    previewBody.setAttribute('href', `${previewBody.dataset.baseUrl}${body.value}.svg?v=detail-1#body`);
  };
  const sync = () => {
    const trailer = kind.value === 'trailer';
    document.getElementById('v_fuel_type').closest('label').hidden = trailer;
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
  sync();
})();
