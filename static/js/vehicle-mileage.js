(() => {
  const group = value => value.replace(/\B(?=(\d{3})+(?!\d))/g, ' ');
  document.querySelectorAll('[data-vehicle-km]').forEach(input => {
    const format = () => {
      const value = input.value.replace(/[ \u00a0\u202f]/g, '');
      if (!/^\d*$/.test(value)) return;
      const cursor = input.selectionStart;
      const digitsBefore = input.value.slice(0, cursor).replace(/\D/g, '').length;
      input.value = group(value);
      if (document.activeElement === input && cursor !== null) {
        let position = 0, digits = 0;
        while (position < input.value.length && digits < digitsBefore) {
          if (/\d/.test(input.value[position])) digits++;
          position++;
        }
        input.setSelectionRange(position, position);
      }
    };
    format();
    input.addEventListener('input', format);
    input.addEventListener('change', format);
  });
  const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)');
  document.querySelectorAll('.mileage-reminder').forEach(reminder => {
    const form = reminder.querySelector('form');
    const error = reminder.querySelector('[data-mileage-error]');
    let pending = false;
    let animation;
    reducedMotion.addEventListener('change', () => {
      if (reducedMotion.matches && animation) animation.finish();
    });
    form.addEventListener('submit', async event => {
      event.preventDefault();
      if (pending) return;
      pending = true;
      error.hidden = true;
      const data = new FormData(form);
      if (event.submitter) data.set(event.submitter.name, event.submitter.value);
      const buttons = Array.from(form.querySelectorAll('button'));
      buttons.forEach(button => { button.disabled = true; });
      form.setAttribute('aria-busy', 'true');
      try {
        const response = await fetch(form.getAttribute('action'), {
          method: 'POST', body: data, headers: { Accept: 'application/json' },
        });
        const result = await response.json();
        if (!response.ok) throw new Error(result.error || 'Не удалось сохранить данные автомобиля. Повторите попытку.');
        if (!Array.isArray(result.vehicle_ids)) throw new Error('Не удалось подтвердить сохранение. Обновите страницу.');
        const remaining = new Set(result.vehicle_ids.map(String));
        const start = getComputedStyle(reminder);
        const from = {
          height: `${reminder.getBoundingClientRect().height}px`, opacity: 1,
          paddingTop: start.paddingTop, paddingBottom: start.paddingBottom,
          marginBottom: start.marginBottom, borderTopWidth: start.borderTopWidth,
          borderBottomWidth: start.borderBottomWidth,
        };
        const fields = Array.from(form.querySelectorAll('[data-mileage-vehicle]'));
        const empty = !fields.some(field => remaining.has(field.dataset.mileageVehicle));
        if (!empty) {
          fields.forEach(field => {
            if (!remaining.has(field.dataset.mileageVehicle)) field.remove();
          });
          form.querySelectorAll('[name="vehicle_ids"]').forEach(input => {
            if (!remaining.has(input.value)) input.remove();
          });
        }
        if (reducedMotion.matches || !reminder.animate) {
          if (empty) reminder.remove();
          return;
        }
        const to = empty ? {
          height: '0px', opacity: 0, paddingTop: '0px', paddingBottom: '0px',
          marginBottom: '0px', borderTopWidth: '0px', borderBottomWidth: '0px',
        } : { ...from, height: `${reminder.getBoundingClientRect().height}px` };
        reminder.style.overflow = 'hidden';
        reminder.inert = true;
        // Приоритетный отступ Bootstrap мешает анимировать схлопывание целиком.
        reminder.classList.remove('mb-4');
        animation = reminder.animate([from, to], {
          duration: 300, easing: 'cubic-bezier(.2,.75,.25,1)', fill: 'both',
        });
        await animation.finished;
        if (empty) reminder.remove();
        animation.cancel();
        animation = null;
        reminder.classList.add('mb-4');
        reminder.style.overflow = '';
        reminder.inert = false;
      } catch (failure) {
        error.textContent = failure instanceof SyntaxError || failure instanceof TypeError
          ? 'Не удалось сохранить данные автомобиля. Повторите попытку.' : failure.message;
        error.hidden = false;
      } finally {
        pending = false;
        buttons.forEach(button => { button.disabled = false; });
        form.removeAttribute('aria-busy');
      }
    });
  });
})();
