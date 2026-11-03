document.querySelectorAll('[data-profile-form]').forEach(form => {
  form.addEventListener('submit', () => {
    const button = form.querySelector('button[type="submit"]');
    button.disabled = true;
    button.setAttribute('aria-busy', 'true');
    const pending = form.querySelector('[data-profile-pending]');
    if (pending) pending.textContent = 'Отправляем тестовое сообщение…';
  });
});
window.addEventListener('pageshow', () => {
  document.querySelectorAll('[data-profile-form] button[type="submit"]').forEach(button => {
    button.disabled = false;
    button.removeAttribute('aria-busy');
  });
  document.querySelectorAll('[data-profile-pending]').forEach(status => { status.textContent = ''; });
});
document.querySelector('[data-profile-bot-link]')?.addEventListener('click', event => {
  const webApp = window.Telegram?.WebApp;
  if (document.body.classList.contains('telegram-mini-app') && typeof webApp?.openTelegramLink === 'function') {
    event.preventDefault();
    webApp.openTelegramLink(event.currentTarget.href);
  }
});
