/* Private image gallery; native dialog supplies keyboard focus containment. */
(() => {
  const dialog = document.createElement('dialog');
  dialog.className = 'vehicle-lightbox';
  dialog.setAttribute('aria-label', 'Просмотр фотографии');
  const close = document.createElement('button');
  close.type = 'button';
  close.className = 'btn btn-secondary';
  close.textContent = 'Закрыть фото';
  const image = document.createElement('img');
  dialog.append(close, image);
  document.body.append(dialog);
  close.addEventListener('click', () => dialog.close());
  dialog.addEventListener('click', event => { if (event.target === dialog) dialog.close(); });
  dialog.addEventListener('close', () => { image.removeAttribute('src'); });
  document.addEventListener('click', event => {
    const link = event.target.closest('[data-vehicle-lightbox]');
    if (!link || event.ctrlKey || event.metaKey || event.shiftKey) return;
    event.preventDefault();
    image.src = link.href;
    image.alt = link.querySelector('img')?.alt || 'Вложение автомобиля';
    dialog.showModal();
  });
  document.querySelectorAll('[data-photo-drop]').forEach(form => {
    const input = form.querySelector('input[type=file]');
    form.addEventListener('dragover', event => { event.preventDefault(); form.classList.add('photo-dragover'); });
    form.addEventListener('dragleave', () => form.classList.remove('photo-dragover'));
    form.addEventListener('drop', event => {
      event.preventDefault();
      form.classList.remove('photo-dragover');
      if (event.dataTransfer.files.length) {
        input.files = event.dataTransfer.files;
        input.dispatchEvent(new Event('change', { bubbles: true }));
      }
    });
  });
})();
