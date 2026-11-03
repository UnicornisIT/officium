(() => {
    const menu = document.querySelector('[data-modules-menu]');
    if (!menu) return;
    const toggle = menu.querySelector('[data-bs-toggle="dropdown"]');
    const dropdown = bootstrap.Dropdown.getOrCreateInstance(toggle);
    const hover = window.matchMedia('(min-width: 992px) and (hover: hover) and (pointer: fine)');
    const panel = menu.querySelector('.dropdown-menu');
    const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)');
    let closeTimer;
    let openedByHover = false;
    let animation;
    let closing = false;
    let completingHide = false;

    const reveal = (opacity = 0, translate = '0 -6px') => {
        animation?.cancel();
        if (!reducedMotion.matches) animation = panel.animate([
            { opacity, translate }, { opacity: 1, translate: '0 0' },
        ], { duration: 180, easing: 'ease-out' });
    };
    toggle.addEventListener('shown.bs.dropdown', () => reveal());
    toggle.addEventListener('hide.bs.dropdown', event => {
        if (completingHide || reducedMotion.matches) return;
        event.preventDefault();
        if (closing) return;
        closing = true;
        const opacity = getComputedStyle(panel).opacity;
        const translate = getComputedStyle(panel).translate;
        animation?.cancel();
        const outgoing = panel.animate([
            { opacity, translate }, { opacity: 0, translate: '0 -4px' },
        ], { duration: 120, easing: 'ease-in', fill: 'forwards' });
        animation = outgoing;
        outgoing.finished.then(() => {
            if (!closing || animation !== outgoing) return;
            closing = false;
            completingHide = true;
            dropdown.hide();
            completingHide = false;
            outgoing.cancel();
        }, () => {}); // Pointer re-entry can reverse a pending close.
    });
    reducedMotion.addEventListener('change', () => {
        if (!reducedMotion.matches) return;
        animation?.cancel();
        if (closing) {
            closing = false;
            dropdown.hide();
        }
    });

    menu.addEventListener('pointerenter', () => {
        window.clearTimeout(closeTimer);
        if (closing) {
            const style = getComputedStyle(panel);
            closing = false;
            reveal(style.opacity, style.translate);
        }
        if (hover.matches && toggle.getAttribute('aria-expanded') !== 'true') {
            openedByHover = true;
            dropdown.show();
        }
    });
    menu.addEventListener('pointerleave', () => {
        if (hover.matches) closeTimer = window.setTimeout(() => dropdown.hide(), 140);
    });
    menu.addEventListener('focusin', () => window.clearTimeout(closeTimer));
    menu.addEventListener('focusout', event => {
        // Safari may blur a focused item without focusing the clicked link.
        // Keep that pointer interaction alive until click; pointerleave still
        // closes the menu, and keyboard focus moving outside closes it below.
        if (!event.relatedTarget && menu.matches(':hover')) return;
        if (!menu.contains(event.relatedTarget)) dropdown.hide();
    });
    menu.addEventListener('keydown', () => {
        // Keyboard navigation deliberately keeps focus on the control.
        openedByHover = false;
    });
    toggle.addEventListener('hidden.bs.dropdown', () => {
        // Bootstrap focuses the toggle even for show() called by pointerenter.
        // Release that artificial focus so :focus-visible cannot pin its label.
        if (openedByHover && document.activeElement === toggle) toggle.blur();
        openedByHover = false;
    });
})();
