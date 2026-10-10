document.addEventListener('DOMContentLoaded', () => {
    const shell = document.querySelector('[data-admin-shell]');
    const sidebar = document.getElementById('adminSidebar');
    const openButton = document.querySelector('[data-admin-sidebar-open]');
    const closeButtons = document.querySelectorAll('[data-admin-sidebar-close]');

    const setSidebarOpen = open => {
        if (!shell || !sidebar) return;
        shell.classList.toggle('admin-shell--menu-open', open);
        document.body.classList.toggle('admin-menu-open', open);
        openButton?.setAttribute('aria-expanded', String(open));
        if (open) sidebar.querySelector('a, button')?.focus();
        else openButton?.focus();
    };

    openButton?.addEventListener('click', () => setSidebarOpen(true));
    closeButtons.forEach(button => button.addEventListener('click', () => setSidebarOpen(false)));
    document.addEventListener('keydown', event => {
        if (event.key === 'Escape' && shell?.classList.contains('admin-shell--menu-open')) {
            setSidebarOpen(false);
        }
    });

    document.querySelectorAll('[data-bs-toggle="tooltip"]').forEach(element => {
        new bootstrap.Tooltip(element, { trigger: 'hover focus click' });
    });

    document.querySelectorAll('[data-admin-auto-submit]').forEach(control => {
        control.addEventListener('change', () => control.form?.requestSubmit());
    });

    document.querySelectorAll('[data-admin-row-href]').forEach(row => {
        row.tabIndex = 0;
        row.addEventListener('click', event => {
            if (event.target.closest('a, button, input, select, textarea, form')) return;
            window.location.assign(row.dataset.adminRowHref);
        });
        row.addEventListener('keydown', event => {
            if ((event.key === 'Enter' || event.key === ' ') && event.target === row) {
                event.preventDefault();
                window.location.assign(row.dataset.adminRowHref);
            }
        });
    });

    const confirmElement = document.getElementById('adminConfirmModal');
    const confirmButton = document.getElementById('adminConfirmSubmit');
    const confirmMessage = document.getElementById('adminConfirmMessage');
    const confirmTitle = document.getElementById('adminConfirmTitle');
    const confirmModal = confirmElement ? new bootstrap.Modal(confirmElement) : null;
    let pendingForm = null;
    let pendingSubmitter = null;

    document.querySelectorAll('form').forEach(form => {
        if (!form.dataset.adminConfirm && !form.querySelector('[data-admin-confirm]')) return;
        form.addEventListener('submit', event => {
            if (form.dataset.confirmed === 'true') {
                delete form.dataset.confirmed;
                return;
            }
            const submitter = event.submitter;
            const message = submitter?.dataset.adminConfirm || form.dataset.adminConfirm;
            if (!message) return;
            event.preventDefault();
            pendingForm = form;
            pendingSubmitter = submitter;
            const title = submitter?.dataset.confirmTitle || form.dataset.confirmTitle;
            const tone = submitter?.dataset.confirmTone || form.dataset.confirmTone;
            if (confirmTitle) confirmTitle.textContent = title || 'Продолжить?';
            if (confirmMessage) confirmMessage.textContent = message;
            confirmButton?.classList.toggle('btn-danger', tone !== 'primary');
            confirmButton?.classList.toggle('btn-primary', tone === 'primary');
            confirmModal?.show();
        });
    });

    confirmButton?.addEventListener('click', () => {
        if (!pendingForm) return;
        pendingForm.dataset.confirmed = 'true';
        confirmModal?.hide();
        pendingForm.requestSubmit(pendingSubmitter || undefined);
        pendingForm = null;
        pendingSubmitter = null;
    });

    confirmElement?.addEventListener('hidden.bs.modal', () => {
        pendingForm = null;
        pendingSubmitter = null;
    });
});
