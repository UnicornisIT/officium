(() => {
  const select = document.getElementById('archiveReason');
  if (!select) return;
  const label = select.closest('label');
  const dropdown = document.createElement('div');
  dropdown.className = 'dropdown';
  const toggle = document.createElement('button');
  toggle.type = 'button';
  toggle.id = 'archiveReasonToggle';
  toggle.className = 'btn btn-outline-secondary dropdown-toggle';
  toggle.dataset.bsToggle = 'dropdown';
  toggle.setAttribute('aria-expanded', 'false');
  toggle.setAttribute('aria-controls', 'archiveReasonMenu');
  const menu = document.createElement('div');
  menu.id = 'archiveReasonMenu';
  menu.className = 'dropdown-menu';
  menu.setAttribute('aria-labelledby', toggle.id);
  const buttons = [...select.options].map(option => {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'dropdown-item';
    button.textContent = option.textContent;
    button.addEventListener('click', () => {
      select.value = option.value;
      select.dispatchEvent(new Event('change', { bubbles: true }));
      toggle.focus();
    });
    menu.append(button);
    return button;
  });
  const sync = () => {
    toggle.textContent = select.selectedOptions[0].textContent;
    buttons.forEach((button, index) => {
      button.classList.toggle('active', select.selectedIndex === index);
      button.setAttribute('aria-pressed', String(select.selectedIndex === index));
    });
  };
  dropdown.append(toggle, menu);
  // Keep the native control as the submitted value and a no-JavaScript fallback.
  // Put enhanced buttons outside the label so choosing an item cannot toggle it again.
  label.after(select, dropdown);
  dropdown.classList.add('vehicle-archive-reason');
  label.htmlFor = toggle.id;
  select.hidden = true;
  select.addEventListener('change', sync);
  sync();
})();
