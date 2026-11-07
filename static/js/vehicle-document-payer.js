document.querySelectorAll('[data-document-payer]').forEach(picker => {
  const input = picker.querySelector('[name="payer"]');
  const toggle = picker.querySelector('[data-bs-toggle="dropdown"]');
  const menu = picker.querySelector('.dropdown-menu');
  const options = menu.querySelectorAll('.dropdown-item');

  menu.addEventListener('click', event => {
    const selected = event.target.closest('.dropdown-item');
    if (!selected) return;

    input.value = selected.value;
    toggle.textContent = selected.textContent;
    options.forEach(option => {
      const active = option === selected;
      option.classList.toggle('active', active);
      option.setAttribute('aria-pressed', String(active));
    });
    toggle.focus();
  });
});
