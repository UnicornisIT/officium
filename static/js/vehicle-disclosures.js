(() => {
  const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)');
  document.querySelectorAll('details.vehicle-disclosure').forEach(details => {
    const summary = details.querySelector(':scope > summary');
    const content = details.querySelector(':scope > form');
    if (!summary || !content) return;
    let expanded = details.open;
    let animation;
    const settle = () => {
      details.open = expanded;
      if (animation) animation.cancel();
      animation = null;
      details.classList.remove('vehicle-disclosure-moving');
    };
    const toggle = next => {
      const startHeight = details.getBoundingClientRect().height;
      if (animation) animation.cancel();
      animation = null;
      expanded = next;
      summary.setAttribute('aria-expanded', String(expanded));
      content.inert = !expanded;
      if (!expanded && content.contains(document.activeElement)) summary.focus();
      if (reducedMotion.matches || !details.animate) {
        settle();
        return;
      }
      details.open = false;
      const closedHeight = details.getBoundingClientRect().height;
      details.open = true;
      const endHeight = expanded ? details.getBoundingClientRect().height : closedHeight;
      details.classList.add('vehicle-disclosure-moving');
      animation = details.animate([
        {height: `${startHeight}px`}, {height: `${endHeight}px`},
      ], {duration: 260, easing: 'cubic-bezier(.2,.75,.25,1)', fill: 'both'});
      animation.onfinish = settle;
    };
    summary.setAttribute('aria-expanded', String(expanded));
    content.inert = !expanded;
    summary.addEventListener('click', event => {
      event.preventDefault();
      toggle(!expanded);
    });
    details.addEventListener('keydown', event => {
      if (event.key === 'Escape' && expanded) {
        event.preventDefault();
        toggle(false);
        summary.focus();
      }
    });
    reducedMotion.addEventListener('change', () => {
      if (reducedMotion.matches && animation) settle();
    });
  });
})();
