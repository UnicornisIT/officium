/*
 * One Telegram Login launcher for every "sign in with Telegram" control.
 *
 * Uses the official Telegram.Login.auth() API from telegram-widget.js: it
 * opens the oauth.telegram.org window directly and hands back the same signed
 * id/auth_date/hash fields the embedded widget used to send to auth_url, so
 * /telegram-login keeps verifying them unchanged. The SDK is preloaded so the
 * window opens inside the click itself and is not caught by popup blockers.
 */
(function () {
    'use strict';

    const script = document.currentScript;
    if (!script) return;

    const SDK_SRC = 'https://telegram.org/js/telegram-widget.js?22';
    const SDK_TIMEOUT_MS = 15000;
    const TRIGGER_SELECTOR = '[data-telegram-login-trigger]';
    const MESSAGES = {
        connecting: 'Подключаем Telegram…',
        signingIn: 'Входим…',
        sdkFailed: 'Не удалось загрузить вход через Telegram. Проверьте соединение и попробуйте ещё раз.',
        popupBlocked: 'Браузер заблокировал окно Telegram. Разрешите всплывающие окна для этого сайта и нажмите кнопку ещё раз.',
        cancelled: 'Вход через Telegram отменён. Нажмите кнопку, чтобы попробовать снова.',
        failed: 'Не удалось открыть окно Telegram. Попробуйте ещё раз.',
        unavailable: 'Вход через Telegram отключён. Обратитесь к администратору.',
    };

    const available = script.dataset.available === 'true';
    const botId = Number.parseInt(script.dataset.botId || '', 10);
    const authUrl = script.dataset.authUrl || '/telegram-login';
    const authOptions = { bot_id: botId, request_access: 'write' };

    let sdkPromise = null;
    let pending = false;
    let popup = null;

    function sdkReady() {
        return Boolean(window.Telegram && window.Telegram.Login && typeof window.Telegram.Login.auth === 'function');
    }

    function loadSdk() {
        if (sdkReady()) return Promise.resolve();
        if (sdkPromise) return sdkPromise;
        sdkPromise = new Promise(function (resolve, reject) {
            const tag = document.createElement('script');
            const timer = window.setTimeout(function () { fail(); }, SDK_TIMEOUT_MS);
            function fail() {
                window.clearTimeout(timer);
                tag.remove();
                sdkPromise = null; // let the next click try again
                reject(new Error('telegram-widget.js unavailable'));
            }
            tag.async = true;
            tag.src = SDK_SRC;
            tag.dataset.telegramLoginSdk = '';
            tag.onload = function () {
                window.clearTimeout(timer);
                if (!sdkReady()) return fail();
                // Mobile browsers without window.opener come back with
                // #tgAuthResult=...; init() decodes it and calls us once.
                try {
                    window.Telegram.Login.init(authOptions, finish);
                } catch (error) { /* nothing to resume */ }
                resolve();
            };
            tag.onerror = fail;
            document.head.appendChild(tag);
        });
        return sdkPromise;
    }

    function statusTarget() {
        return document.getElementById('telegram-login-status');
    }

    function report(message, tone) {
        const target = statusTarget();
        if (target) {
            target.textContent = message || '';
            target.hidden = !message;
            target.classList.toggle('text-danger', tone === 'danger');
            target.classList.toggle('text-muted', tone !== 'danger');
            return;
        }
        if (message && typeof window.showToast === 'function') {
            window.showToast(message, tone === 'danger' ? 'danger' : 'info');
        }
    }

    function setBusy(busy) {
        pending = busy;
        document.querySelectorAll(TRIGGER_SELECTOR).forEach(function (trigger) {
            if (busy) trigger.setAttribute('aria-busy', 'true');
            else trigger.removeAttribute('aria-busy');
        });
    }

    function finish(user) {
        popup = null;
        if (user && user.id && user.hash && user.auth_date) {
            report(MESSAGES.signingIn);
            // Pass every signed field back as-is: the server rebuilds the
            // data-check-string from all of them.
            const params = new URLSearchParams();
            Object.keys(user).forEach(function (key) {
                if (user[key] !== null && user[key] !== undefined) params.append(key, String(user[key]));
            });
            window.location.assign(authUrl + '?' + params.toString());
            return;
        }
        setBusy(false);
        report(MESSAGES.cancelled);
    }

    function openWindow() {
        // Telegram.Login.auth() opens its window synchronously but does not
        // return it; look at what window.open hands back only to tell a
        // blocked popup apart from an open one.
        const nativeOpen = window.open;
        let opened = null;
        window.open = function () {
            opened = nativeOpen.apply(window, arguments);
            return opened;
        };
        try {
            window.Telegram.Login.auth(authOptions, finish);
        } catch (error) {
            setBusy(false);
            report(MESSAGES.failed, 'danger');
            return;
        } finally {
            window.open = nativeOpen;
        }
        if (!opened) {
            setBusy(false);
            report(MESSAGES.popupBlocked, 'danger');
            return;
        }
        popup = opened;
        report('');
    }

    function start() {
        if (pending) {
            if (popup && !popup.closed) popup.focus();
            return;
        }
        setBusy(true);
        if (sdkReady()) {
            openWindow();
            return;
        }
        report(MESSAGES.connecting);
        loadSdk().then(openWindow, function () {
            setBusy(false);
            report(MESSAGES.sdkFailed, 'danger');
        });
    }

    function showUnavailable() {
        const notice = document.querySelector('[data-telegram-login-unavailable]');
        if (notice) {
            notice.scrollIntoView({ block: 'center', behavior: 'smooth' });
            notice.focus({ preventScroll: true });
            return true;
        }
        return false;
    }

    document.addEventListener('click', function (event) {
        const trigger = event.target.closest(TRIGGER_SELECTOR);
        if (!trigger || event.defaultPrevented || event.button !== 0) return;
        if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
        if (!available || !botId) {
            // On /login point at the "disabled" notice instead of reloading;
            // elsewhere the link still leads to /login, which explains it.
            if (showUnavailable()) {
                event.preventDefault();
            } else if (trigger.tagName !== 'A') {
                report(MESSAGES.unavailable, 'danger');
            }
            return;
        }
        event.preventDefault();
        start();
    });

    window.OfficiumTelegramLogin = { start: start };

    if (available && botId) {
        loadSdk().catch(function () { /* reported on click */ });
    }
}());
