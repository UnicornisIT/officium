document.querySelectorAll('[data-document-amount]').forEach(input => {
  const format = (pad = false) => {
    const raw = input.value.replace(/\s/g, '');
    if (!raw || !/^\d+(?:[.,]\d{0,2})?$/.test(raw)) return;
    const position = input.selectionStart ?? input.value.length;
    const before = input.value.slice(0, position).replace(/\s/g, '').length;
    const [integer, fraction] = raw.replace('.', ',').split(',');
    const grouped = integer.replace(/\B(?=(\d{3})+(?!\d))/g, ' ');
    input.value = grouped + (pad ? ',' + (fraction || '').padEnd(2, '0') : fraction === undefined ? '' : ',' + fraction);
    if (document.activeElement === input && !pad) {
      let cursor = 0, count = 0;
      while (cursor < input.value.length && count < before) {
        if (input.value[cursor] !== ' ') count++;
        cursor++;
      }
      input.setSelectionRange(cursor, cursor);
    }
  };
  input.addEventListener('input', () => format());
  input.addEventListener('blur', () => format(true));
  format(true);
});
