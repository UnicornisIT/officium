(() => {
  const nav = document.querySelector('.vehicle-navigation');
  if (!nav) return;
  const more = nav.querySelector('.vehicle-navigation-more');
  const toggle = more.querySelector('button');
  const menu = more.querySelector('.dropdown-menu');
  const primary = [...nav.querySelectorAll('[data-vehicle-primary]')];
  const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)');
  let animation;
  let closing = false;
  let finishingHide = false;

  toggle.addEventListener('shown.bs.dropdown', () => {
    animation?.cancel();
    if (!reducedMotion.matches) {
      // Individual translate leaves Popper's positioning transform intact.
      animation = menu.animate([
        { opacity: 0, translate: '0 -6px' },
        { opacity: 1, translate: '0 0' },
      ], { duration: 180, easing: 'ease-out' });
    }
  });
  toggle.addEventListener('hide.bs.dropdown', event => {
    if (finishingHide || reducedMotion.matches) return;
    event.preventDefault();
    if (closing) return;
    closing = true;
    animation?.cancel();
    animation = menu.animate([
      { opacity: 1, translate: '0 0' },
      { opacity: 0, translate: '0 -4px' },
    ], { duration: 120, easing: 'ease-in', fill: 'forwards' });
    animation.finished.then(() => {
      closing = false;
      finishingHide = true;
      bootstrap.Dropdown.getOrCreateInstance(toggle).hide();
      finishingHide = false;
      animation.cancel();
    });
  });

  function fit() {
    // Reuse the same links and URLs in both locations, including aria-current.
    primary.forEach(link => {
      link.classList.replace('dropdown-item', 'nav-link');
      nav.insertBefore(link, more);
    });
    const gap = parseFloat(getComputedStyle(nav).columnGap) || 0;
    let width = more.getBoundingClientRect().width + primary.reduce(
      (sum, link) => sum + link.getBoundingClientRect().width + gap, 0);
    for (let index = primary.length - 1; index >= 1 && width > nav.clientWidth; index--) {
      const link = primary[index];
      width -= link.getBoundingClientRect().width + gap;
      link.classList.replace('nav-link', 'dropdown-item');
      menu.prepend(link);
    }
    toggle.classList.toggle('active', !!menu.querySelector('[aria-current="page"]'));
  }

  let frame;
  const schedule = () => {
    cancelAnimationFrame(frame);
    frame = requestAnimationFrame(fit);
  };
  new ResizeObserver(schedule).observe(nav);
  document.fonts.ready.then(schedule);
  fit();
})();
