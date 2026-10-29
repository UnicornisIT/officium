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
})();
