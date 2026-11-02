/* Enhance the native file input without replacing its form or accessibility API. */
(() => {
    function enhanceFiles(root = document) {
        root.querySelectorAll('input[type="file"]').forEach(input => {
            if (input.closest('.ui-file-picker')) return;
            const picker = document.createElement('span');
            picker.className = 'ui-file-picker';
            const button = document.createElement('span');
            button.className = 'ui-file-picker__button';
            button.textContent = input.multiple ? 'Выбрать файлы' : 'Выбрать файл';
            button.setAttribute('aria-hidden', 'true');
            const name = document.createElement('span');
            name.className = 'ui-file-picker__name';
            name.setAttribute('aria-live', 'polite');
            const sync = () => {
                name.textContent = input.files.length ? [...input.files].map(file => file.name).join(', ') : 'Файл не выбран';
                name.title = name.textContent;
            };
            input.before(picker);
            picker.append(button, name, input);
            input.addEventListener('change', sync);
            input.form?.addEventListener('reset', () => setTimeout(sync, 0));
            sync();
        });
    }
    enhanceFiles();

    let pending = false;
    window.confirmAction = message => new Promise(resolve => {
        if (pending) { resolve(false); return; }
        pending = true;
        const previous = document.querySelector('.modal.show');
        const focus = document.activeElement;
        const element = document.createElement('div');
        element.className = 'modal ui-confirm-dialog';
        element.tabIndex = -1;
        element.setAttribute('aria-labelledby', 'uiConfirmTitle');
        element.setAttribute('aria-describedby', 'uiConfirmMessage');
        element.innerHTML = '<div class="modal-dialog modal-dialog-centered"><div class="modal-content"><div class="modal-header"><h2 class="modal-title fs-5" id="uiConfirmTitle">Подтвердите действие</h2><button type="button" class="btn-close" data-bs-dismiss="modal" aria-label="Закрыть"></button></div><div class="modal-body" id="uiConfirmMessage"></div><div class="modal-footer"><button type="button" class="btn btn-outline-secondary" data-bs-dismiss="modal">Отмена</button><button type="button" class="btn btn-primary" data-confirm-accept>Продолжить</button></div></div></div>';
        element.querySelector('#uiConfirmMessage').textContent = message;
        document.body.append(element);
        const modal = new bootstrap.Modal(element);
        let accepted = false;
        element.querySelector('[data-confirm-accept]').addEventListener('click', () => { accepted = true; modal.hide(); });
        element.addEventListener('shown.bs.modal', () => element.querySelector('[data-bs-dismiss]').focus(), {once:true});
        element.addEventListener('hidden.bs.modal', () => {
            modal.dispose(); element.remove(); pending = false;
            if (!accepted && previous) bootstrap.Modal.getOrCreateInstance(previous).show();
            if (!accepted && focus?.isConnected) focus.focus();
            resolve(accepted);
        }, {once:true});
        if (previous) {
            previous.addEventListener('hidden.bs.modal', () => modal.show(), {once:true});
            bootstrap.Modal.getOrCreateInstance(previous).hide();
        } else modal.show();
    });
    document.querySelectorAll('form[data-ui-confirm]').forEach(form => {
        form.addEventListener('submit', async event => {
            if (form.dataset.uiConfirmed === 'true') { delete form.dataset.uiConfirmed; return; }
            event.preventDefault();
            const submitter = event.submitter;
            if (await window.confirmAction(form.dataset.uiConfirm)) {
                form.dataset.uiConfirmed = 'true';
                form.requestSubmit(submitter);
            }
        });
    });
})();
