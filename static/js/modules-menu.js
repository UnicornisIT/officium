(() => {
    const menu = document.querySelector('[data-modules-menu]');
    if (!menu) return;
    const toggle = menu.querySelector('[data-bs-toggle="dropdown"]');
    const dropdown = bootstrap.Dropdown.getOrCreateInstance(toggle);
    const hover = window.matchMedia('(min-width: 992px) and (hover: hover) and (pointer: fine)');
    let closeTimer;

    menu.addEventListener('pointerenter', () => {
        window.clearTimeout(closeTimer);
        if (hover.matches) dropdown.show();
    });
    menu.addEventListener('pointerleave', () => {
        if (hover.matches) closeTimer = window.setTimeout(() => dropdown.hide(), 140);
    });
    menu.addEventListener('focusin', () => window.clearTimeout(closeTimer));
    menu.addEventListener('focusout', event => {
        if (!menu.contains(event.relatedTarget)) dropdown.hide();
    });
})();
