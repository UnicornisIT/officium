(() => {
  const element = document.getElementById('wishlistModal');
  if (!element) return;
  const modal = bootstrap.Modal.getOrCreateInstance(element);
  let trigger;
  document.querySelectorAll('[data-wishlist-open]').forEach(button => {
    button.addEventListener('click', () => {
      trigger = button;
      const menu = button.closest('.offcanvas');
      if (menu?.classList.contains('show')) {
        menu.addEventListener('hidden.bs.offcanvas', () => modal.show(button), { once: true });
        bootstrap.Offcanvas.getOrCreateInstance(menu).hide();
      } else {
        modal.show(button);
      }
    });
  });
  element.addEventListener('hidden.bs.modal', () => {
    const target = trigger?.getClientRects().length && getComputedStyle(trigger).visibility !== 'hidden'
      ? trigger : document.querySelector('#modulesMenuToggle, [data-bs-target="#telegramMoreMenu"]');
    target?.focus();
  });
})();
