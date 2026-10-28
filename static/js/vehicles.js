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
  const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)');
  const motionOptions = { duration: 300, easing: 'cubic-bezier(.2,.75,.25,1)', fill: 'both' };
  const fieldAnimations = new Map();
  const showField = (element, visible, animated) => {
    const previous = fieldAnimations.get(element);
    if (!previous && element.hidden === !visible) return;
    const startHeight = element.getBoundingClientRect().height;
    const startOpacity = element.hidden ? 0 : Number(getComputedStyle(element).opacity);
    const startPadding = element.hidden ? 0 : parseFloat(getComputedStyle(element).paddingTop);
    if (previous) previous.cancel();
    fieldAnimations.delete(element);
    element.classList.remove('vehicle-revealing');
    element.inert = !visible;
    if (!animated || reducedMotion.matches) {
      element.hidden = !visible;
      return;
    }
    element.hidden = false;
    const targetHeight = visible ? element.getBoundingClientRect().height : 0;
    const targetPadding = visible ? parseFloat(getComputedStyle(element).paddingTop) : 0;
    element.classList.add('vehicle-revealing');
    const animation = element.animate([
      { height: `${startHeight}px`, paddingTop: `${startPadding}px`, opacity: startOpacity },
      { height: `${targetHeight}px`, paddingTop: `${targetPadding}px`, opacity: visible ? 1 : 0 },
    ], motionOptions);
    fieldAnimations.set(element, animation);
    animation.onfinish = () => {
      if (fieldAnimations.get(element) !== animation) return;
      element.hidden = !visible;
      animation.cancel();
      fieldAnimations.delete(element);
      element.classList.remove('vehicle-revealing');
    };
  };
  const summary = picker.querySelector('summary');
  const panel = picker.querySelector('.vehicle-color-panel');
  let pickerExpanded = picker.open;
  let pickerAnimation;
  const setPickerOpen = expanded => {
    pickerExpanded = expanded;
    summary.setAttribute('aria-expanded', String(expanded));
    panel.inert = !expanded;
    const startHeight = picker.getBoundingClientRect().height;
    if (pickerAnimation) pickerAnimation.cancel();
    pickerAnimation = null;
    picker.classList.remove('vehicle-revealing');
    if (reducedMotion.matches) {
      picker.open = expanded;
      return;
    }
    // Keep details open during closing so its content can shrink smoothly.
    picker.open = false;
    const closedHeight = picker.getBoundingClientRect().height;
    picker.open = true;
    const targetHeight = expanded ? picker.getBoundingClientRect().height : closedHeight;
    picker.classList.add('vehicle-revealing');
    const animation = picker.animate([{ height: `${startHeight}px` }, { height: `${targetHeight}px` }], motionOptions);
    pickerAnimation = animation;
    animation.onfinish = () => {
      if (pickerAnimation !== animation) return;
      picker.open = expanded;
      animation.cancel();
      pickerAnimation = null;
      picker.classList.remove('vehicle-revealing');
    };
  };
  summary.setAttribute('aria-expanded', String(pickerExpanded));
  panel.inert = !pickerExpanded;
  summary.addEventListener('click', event => {
    event.preventDefault();
    setPickerOpen(!pickerExpanded);
  });
  reducedMotion.addEventListener('change', () => {
    if (!reducedMotion.matches) return;
    if (pickerAnimation) pickerAnimation.finish();
    for (const animation of fieldAnimations.values()) animation.finish();
  });
  const updatePreview = () => {
    preview.style.color = color.value;
    picker.style.setProperty('--vehicle-color', color.value);
    swatches.forEach(button => button.setAttribute('aria-pressed', String(button.dataset.vehicleColor === color.value.toLowerCase())));
    previewBody.setAttribute('href', `${previewBody.dataset.baseUrl}${body.value}.svg?v=detail-2#body`);
  };
  const sync = (animated = false) => {
    const trailer = kind.value === 'trailer';
    document.getElementById('v_fuel_type').closest('.vehicle-field').hidden = trailer;
    document.querySelectorAll('[data-trailer]').forEach(el => showField(el, trailer, animated));
    document.querySelectorAll('[data-powered]').forEach(el => { el.hidden = trailer; });
    document.querySelectorAll('[data-tractor]').forEach(el => showField(el, kind.value === 'tractor_unit', animated));
    const trailerName = document.getElementById('v_trailer_name');
    if (trailerName) trailerName.disabled = kind.value !== 'tractor_unit';
    if (trailer) {
      document.getElementById('v_fuel_type').value = 'none';
      document.getElementById('v_body_type').value = 'trailer';
    }
    if (kind.value === 'tractor_unit') document.getElementById('v_body_type').value = 'tractor_unit';
    if (!trailer) document.getElementById('v_tractor').value = '';
    updatePreview();
  };
  kind.addEventListener('change', () => sync(true));
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
      setPickerOpen(false);
      picker.querySelector('summary').focus();
    }
  });
  sync();
})();
