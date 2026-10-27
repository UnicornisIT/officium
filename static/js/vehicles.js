(() => {
  const kind = document.getElementById('v_kind');
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
  };
  kind.addEventListener('change', sync);
  sync();
})();
