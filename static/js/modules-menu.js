(() => {
    const menu = document.querySelector('[data-modules-menu]');
    if (!menu) return;
    const toggle = menu.querySelector('[data-bs-toggle="dropdown"]');
    const dropdown = bootstrap.Dropdown.getOrCreateInstance(toggle);
    const hover = window.matchMedia('(min-width: 992px) and (hover: hover) and (pointer: fine)');
    let closeTimer;
    let openedByHover = false;

    menu.addEventListener('pointerenter', () => {
        window.clearTimeout(closeTimer);
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
