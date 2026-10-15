/* ============================================================
   officium — Основной JavaScript
   Все взаимодействия: модалки, AJAX, платежи, уведомления
============================================================ */

// ── Глобальные переменные ──
let currentDebtId = null;       // ID долга для операций
let pendingArchiveId = null;    // ID долга, ожидающего архивации
let currentEditPayment = null;
let paymentHistoryCache = new Map();
let currentScheduleDebt = null;
let currentSplitPurchaseDebtId = null;
let currentMinimumPaymentAmount = null;
let currentMinimumPaymentDate = null;
let currentPlannedEarlyRepaymentAmount = null;

// ── Bootstrap объекты ──
const debtModalEl     = document.getElementById('debtModal');
const paymentModalEl  = document.getElementById('paymentModal');
const editPaymentModalEl = document.getElementById('editPaymentModal');
const historyModalEl  = document.getElementById('historyModal');
const scheduleModalEl = document.getElementById('scheduleModal');
const splitPurchaseModalEl = document.getElementById('splitPurchaseModal');
const archiveConfirmEl = document.getElementById('archiveConfirmModal');

const debtModal       = debtModalEl ? new bootstrap.Modal(debtModalEl) : null;
const paymentModal    = paymentModalEl ? new bootstrap.Modal(paymentModalEl) : null;
const editPaymentModal = editPaymentModalEl ? new bootstrap.Modal(editPaymentModalEl) : null;
const historyModal    = historyModalEl ? new bootstrap.Modal(historyModalEl) : null;
const scheduleModal   = scheduleModalEl ? new bootstrap.Modal(scheduleModalEl) : null;
const splitPurchaseModal = splitPurchaseModalEl ? new bootstrap.Modal(splitPurchaseModalEl) : null;
const archiveConfirmModal = archiveConfirmEl ? new bootstrap.Modal(archiveConfirmEl) : null;

// ══════════════════════════════════════════════════════════
// УТИЛИТЫ
// ══════════════════════════════════════════════════════════

/**
 * Показывает Toast-уведомление
 * @param {string} message - текст
 * @param {'success'|'danger'|'warning'|'info'} type - тип
 */
function showToast(message, type = 'success') {
    const toast = document.getElementById('mainToast');
    const toastMsg = document.getElementById('toastMessage');
    toastMsg.textContent = message;
    toast.className = `toast align-items-center border-0 toast-${type}`;

    const icons = { success: '✓ ', danger: '✕ ', warning: '⚠ ', info: 'ℹ ' };
    toastMsg.textContent = (icons[type] || '') + message;

    const bsToast = new bootstrap.Toast(toast, { delay: 3500 });
    bsToast.show();
}

function applyTheme(theme) {
    document.documentElement.setAttribute('data-theme', theme);
    document.documentElement.setAttribute('data-bs-theme', theme);
    localStorage.setItem('officiumTheme', theme);
    const themeColor = theme === 'dark' ? '#08101c' : '#f4f6fb';
    document.querySelectorAll('meta[name="theme-color"]').forEach((meta) => {
        meta.setAttribute('content', themeColor);
    });

    const btn = document.getElementById('themeToggleBtn');
    if (!btn) return;

    if (theme === 'dark') {
        btn.innerHTML = '<span class="nav-expand__compact"><i class="bi bi-sun-fill me-1"></i>Светлая тема</span><span class="nav-expand__full" aria-hidden="true"><i class="bi bi-sun-fill"></i>Светлая тема</span>';
        btn.title = 'Переключиться в светлую тему';
    } else {
        btn.innerHTML = '<span class="nav-expand__compact"><i class="bi bi-moon-fill me-1"></i>Тёмная тема</span><span class="nav-expand__full" aria-hidden="true"><i class="bi bi-moon-fill"></i>Тёмная тема</span>';
        btn.title = 'Переключиться в тёмную тему';
    }
}

function getTelegramWebApp() {
    if (!document.body.classList.contains('telegram-mini-app')) return null;
    return window.Telegram && window.Telegram.WebApp ? window.Telegram.WebApp : null;
}

function applyTelegramTheme() {
    const webApp = getTelegramWebApp();
    if (!webApp) return;

    const theme = webApp.colorScheme === 'light' ? 'light' : 'dark';
    document.documentElement.setAttribute('data-theme', theme);
    document.documentElement.setAttribute('data-bs-theme', theme);

    const params = webApp.themeParams || {};
    const rootStyle = document.documentElement.style;
    const themeVariables = {
        '--tg-theme-bg-color': params.bg_color,
        '--tg-theme-secondary-bg-color': params.secondary_bg_color,
        '--tg-theme-text-color': params.text_color,
        '--tg-theme-hint-color': params.hint_color,
        '--tg-theme-link-color': params.link_color,
        '--tg-theme-button-color': params.button_color,
        '--tg-theme-button-text-color': params.button_text_color,
        '--tg-theme-header-bg-color': params.header_bg_color,
        '--tg-theme-section-bg-color': params.section_bg_color,
        '--tg-theme-section-header-text-color': params.section_header_text_color,
        '--tg-theme-subtitle-text-color': params.subtitle_text_color,
        '--tg-theme-destructive-text-color': params.destructive_text_color,
    };
    Object.entries(themeVariables).forEach(([name, value]) => {
        if (value) rootStyle.setProperty(name, value);
    });

    try {
        if (params.header_bg_color) webApp.setHeaderColor(params.header_bg_color);
        if (params.bg_color) webApp.setBackgroundColor(params.bg_color);
        if (params.secondary_bg_color) webApp.setBottomBarColor(params.secondary_bg_color);
    } catch (error) {
        // Older Telegram clients may not support every color method.
    }
}

let telegramViewportFrame = null;
let telegramStableHeightPending = false;

function writeTelegramViewport() {
    telegramViewportFrame = null;
    const webApp = getTelegramWebApp();
    if (!webApp) return;

    const rootStyle = document.documentElement.style;
    if (webApp.viewportHeight) rootStyle.setProperty('--tg-viewport-height', `${webApp.viewportHeight}px`);
    if (telegramStableHeightPending && webApp.viewportStableHeight) {
        rootStyle.setProperty('--tg-viewport-stable-height', `${webApp.viewportStableHeight}px`);
    }
    telegramStableHeightPending = false;
}

// viewportChanged fires many times per gesture. Only the height is synced here,
// at most once per frame, and the stable height (which drives the page
// min-height) only once Telegram reports the viewport as settled.
function syncTelegramViewport(event) {
    if (!event || event.isStateStable === true) telegramStableHeightPending = true;

    if (!event) {
        // Initial call: write synchronously so initMobileViewport() can read it.
        if (telegramViewportFrame !== null) cancelAnimationFrame(telegramViewportFrame);
        writeTelegramViewport();
        return;
    }
    if (telegramViewportFrame === null) {
        telegramViewportFrame = requestAnimationFrame(writeTelegramViewport);
    }
}

function initTelegramMiniApp() {
    const webApp = getTelegramWebApp();
    if (!webApp) return;

    webApp.ready();
    webApp.expand();
    if (typeof webApp.disableVerticalSwipes === 'function') webApp.disableVerticalSwipes();
    applyTelegramTheme();
    syncTelegramViewport();
    webApp.onEvent('themeChanged', applyTelegramTheme);
    webApp.onEvent('viewportChanged', syncTelegramViewport);

    let activeOverlay = null;
    const hasPageBackTarget = window.location.pathname !== '/';
    const syncBackButton = () => {
        if (activeOverlay || hasPageBackTarget) webApp.BackButton.show();
        else webApp.BackButton.hide();
    };
    webApp.BackButton.onClick(() => {
        if (activeOverlay) {
            const overlay = activeOverlay;
            activeOverlay = null;
            if (overlay.classList.contains('modal')) {
                bootstrap.Modal.getInstance(overlay)?.hide();
            } else {
                bootstrap.Offcanvas.getInstance(overlay)?.hide();
            }
            syncBackButton();
            return;
        }
        if (window.history.length > 1) window.history.back();
        else window.location.assign('/');
    });

    document.querySelectorAll('.modal, .offcanvas').forEach(overlay => {
        overlay.addEventListener(overlay.classList.contains('modal') ? 'shown.bs.modal' : 'shown.bs.offcanvas', () => {
            activeOverlay = overlay;
            syncBackButton();
        });
        overlay.addEventListener(overlay.classList.contains('modal') ? 'hidden.bs.modal' : 'hidden.bs.offcanvas', () => {
            if (activeOverlay === overlay) activeOverlay = null;
            syncBackButton();
        });
    });
    syncBackButton();

    document.querySelectorAll('.telegram-bottom-nav a, .telegram-bottom-nav__add, .telegram-app-bar__menu').forEach(control => {
        control.addEventListener('click', () => {
            if (webApp.HapticFeedback) webApp.HapticFeedback.impactOccurred('light');
        });
    });
}

function initMobileViewport() {
    const root = document.documentElement;
    const body = document.body;
    const visualViewport = window.visualViewport;
    const editableSelector = 'input:not([type="hidden"]):not([disabled]), textarea:not([disabled]), select:not([disabled]), [contenteditable="true"]';
    let activeField = null;
    let animationFrame = null;
    let stableHeight = Math.max(window.innerHeight, visualViewport ? visualViewport.height : 0);
    const delayedChecks = new Set();

    const readTelegramStableHeight = () => parseFloat(
        getComputedStyle(root).getPropertyValue('--tg-viewport-stable-height')
    );
    const telegramStableHeight = readTelegramStableHeight();
    if (Number.isFinite(telegramStableHeight)) stableHeight = Math.max(stableHeight, telegramStableHeight);

    const viewportMetrics = () => ({
        height: visualViewport ? visualViewport.height : window.innerHeight,
        offsetTop: visualViewport ? visualViewport.offsetTop : 0,
    });

    const isEditable = element => element instanceof Element && element.matches(editableSelector);

    const updateViewportState = () => {
        const metrics = viewportMetrics();
        if (!activeField) {
            const currentTelegramStableHeight = readTelegramStableHeight();
            stableHeight = Math.max(
                window.innerHeight,
                metrics.height + metrics.offsetTop,
                Number.isFinite(currentTelegramStableHeight) ? currentTelegramStableHeight : 0,
            );
        }

        const keyboardInset = activeField
            ? Math.max(0, stableHeight - metrics.height - metrics.offsetTop)
            : 0;
        const keyboardOpen = Boolean(activeField && keyboardInset > 80);

        root.style.setProperty('--app-visual-viewport-height', `${Math.round(metrics.height)}px`);
        root.style.setProperty('--app-viewport-offset-top', `${Math.round(metrics.offsetTop)}px`);
        root.style.setProperty('--app-stable-viewport-height', `${Math.round(stableHeight)}px`);
        root.style.setProperty('--app-keyboard-inset', `${Math.round(keyboardInset)}px`);
        body.classList.toggle('keyboard-open', keyboardOpen);
        return { ...metrics, keyboardOpen };
    };

    const fieldGroup = field => field.closest('.mb-3, .form-floating, .input-group, .form-check') || field;

    const revealActiveField = smooth => {
        animationFrame = null;
        if (!activeField || !document.contains(activeField)) return;

        const metrics = updateViewportState();
        const target = fieldGroup(activeField);
        let rect = target.getBoundingClientRect();
        const fieldRect = activeField.getBoundingClientRect();
        const visibleTop = metrics.offsetTop + 12;
        const visibleBottom = metrics.offsetTop + metrics.height - 18;

        // Very tall groups (for example a textarea with help text) should still
        // prioritize the label and the focused control rather than their footer.
        if (rect.height > metrics.height - 30) {
            rect = {
                top: Math.min(rect.top, fieldRect.top - 34),
                bottom: fieldRect.bottom,
            };
        }

        let delta = 0;
        if (rect.bottom > visibleBottom) delta = rect.bottom - visibleBottom;
        if (rect.top - delta < visibleTop) delta = rect.top - visibleTop;
        if (Math.abs(delta) < 2) return;

        const modalScroller = activeField.closest('.modal-body');
        const behavior = smooth && !window.matchMedia('(prefers-reduced-motion: reduce)').matches
            ? 'smooth'
            : 'auto';
        if (modalScroller && modalScroller.scrollHeight > modalScroller.clientHeight) {
            modalScroller.scrollBy({ top: delta, behavior });
        } else {
            window.scrollBy({ top: delta, behavior });
        }
    };

    const queueReveal = (smooth = false) => {
        if (animationFrame !== null) cancelAnimationFrame(animationFrame);
        animationFrame = requestAnimationFrame(() => revealActiveField(smooth));
    };

    const scheduleDelayedReveal = delay => {
        const timer = window.setTimeout(() => {
            delayedChecks.delete(timer);
            if (activeField) queueReveal(false);
        }, delay);
        delayedChecks.add(timer);
    };

    document.addEventListener('focusin', event => {
        if (!isEditable(event.target)) return;
        activeField = event.target;
        updateViewportState();
        queueReveal(true);
        scheduleDelayedReveal(90);
        scheduleDelayedReveal(280);
    });

    document.addEventListener('focusout', () => {
        window.setTimeout(() => {
            activeField = isEditable(document.activeElement) ? document.activeElement : null;
            if (!activeField) {
                delayedChecks.forEach(timer => clearTimeout(timer));
                delayedChecks.clear();
            }
            updateViewportState();
            if (activeField) queueReveal(false);
        }, 120);
    });

    const handleViewportChange = () => {
        updateViewportState();
        if (activeField) queueReveal(false);
    };
    if (visualViewport) {
        visualViewport.addEventListener('resize', handleViewportChange);
        visualViewport.addEventListener('scroll', handleViewportChange);
    }
    window.addEventListener('resize', handleViewportChange);
    window.addEventListener('orientationchange', () => {
        window.setTimeout(() => {
            const metrics = viewportMetrics();
            const currentTelegramStableHeight = readTelegramStableHeight();
            stableHeight = Math.max(
                window.innerHeight,
                metrics.height + metrics.offsetTop,
                Number.isFinite(currentTelegramStableHeight) ? currentTelegramStableHeight : 0,
            );
            handleViewportChange();
        }, 280);
    });
    updateViewportState();
}

function toggleTheme() {
    const current = document.documentElement.getAttribute('data-theme') || 'dark';
    applyTheme(current === 'dark' ? 'light' : 'dark');
}

function initThemeToggle() {
    if (getTelegramWebApp()) {
        applyTelegramTheme();
        const telegramThemeButton = document.getElementById('themeToggleBtn');
        if (telegramThemeButton) telegramThemeButton.hidden = true;
        return;
    }
    const storedTheme = localStorage.getItem('officiumTheme') || 'dark';
    applyTheme(storedTheme);

    const btn = document.getElementById('themeToggleBtn');
    if (btn) {
        btn.addEventListener('click', toggleTheme);
    }
}

/**
 * Форматирует число как валюту
 */
function getCsrfToken() {
    return document.querySelector('meta[name="csrf-token"]')?.content || '';
}

async function jsonFetch(url, options = {}) {
    const token = getCsrfToken();
    const headers = options.headers || {};
    if (token && !headers['X-CSRFToken'] && !headers['X-CSRF-Token']) {
        headers['X-CSRFToken'] = token;
    }
    const response = await fetch(url, { ...options, headers });
    if (response.status === 401 && document.querySelector('.impersonation-banner')) {
        // The impersonation session ended (time limit); the server already restored the admin.
        window.location.assign('/admin');
    }
    return response;
}

function initCommentTemplates() {
    document.addEventListener('click', (event) => {
        const button = event.target.closest('[data-comment-template]');
        if (!button) return;
        const field = document.getElementById(button.dataset.commentTarget);
        if (!field) return;
        const text = button.dataset.commentTemplate;
        field.value = field.value.trim() ? `${field.value.trimEnd()}\n${text}` : text;
        field.focus();
    });
}

function initImpersonationBanner() {
    const banner = document.querySelector('.impersonation-banner[data-expires-at]');
    if (!banner) return;
    const expiresAt = Date.parse(banner.dataset.expiresAt);
    const minutesNode = banner.querySelector('[data-impersonation-minutes]');
    if (Number.isNaN(expiresAt)) return;
    const tick = () => {
        const remaining = expiresAt - Date.now();
        if (remaining <= 0) {
            // A fresh request lets the server end the session and return to the admin panel.
            window.location.reload();
            return;
        }
        if (minutesNode) minutesNode.textContent = String(Math.ceil(remaining / 60000));
    };
    tick();
    window.setInterval(tick, 15000);
}

function formatMoney(n) {
    if (n == null) return '—';
    const normalized = String(n).replace(/\s+/g, '').replace(',', '.');
    const value = Number(normalized);
    if (Number.isNaN(value)) return '—';
    const hasFraction = !Number.isInteger(value);
    return value.toLocaleString('ru-RU', {
        minimumFractionDigits: hasFraction ? 2 : 0,
        maximumFractionDigits: 2,
    }) + ' ₽';
}

function initNavbarLayout() {
    const navbar = document.querySelector('.navbar');
    if (!navbar) return;

    const updateLayout = () => {
        navbar.classList.remove('navbar--compact');
        if (window.innerWidth < 992) return;

        const container = navbar.querySelector('.container-fluid');
        const brand = navbar.querySelector('.navbar-brand');
        const nav = navbar.querySelector('.navbar-nav');
        if (!container || !brand || !nav) return;

        const outerWidth = element => {
            const style = window.getComputedStyle(element);
            return element.getBoundingClientRect().width
                + (Number.parseFloat(style.marginLeft) || 0)
                + (Number.parseFloat(style.marginRight) || 0);
        };
        const containerStyle = window.getComputedStyle(container);
        const navStyle = window.getComputedStyle(nav);
        const containerGap = Number.parseFloat(containerStyle.columnGap)
            || Number.parseFloat(containerStyle.gap)
            || 0;
        const navGap = Number.parseFloat(navStyle.columnGap)
            || Number.parseFloat(navStyle.gap)
            || 0;
        const items = Array.from(nav.children)
            .filter(item => window.getComputedStyle(item).display !== 'none');
        const requiredWidth = items.reduce((total, item) => total + outerWidth(item), 0)
            + Math.max(0, items.length - 1) * navGap;
        const availableWidth = container.clientWidth - outerWidth(brand) - containerGap;
        // 1920px desktop viewports become ~1745 CSS px at browser zoom 110%.
        // Switch before that boundary so labels never reach a wrapped/clipped
        // intermediate state while the browser is recalculating its viewport.
        const needsZoomSafeCompact = window.innerWidth <= 1850;
        const hasOverflow = needsZoomSafeCompact
            || requiredWidth > availableWidth - 2
            || document.documentElement.scrollWidth > window.innerWidth + 1;
        navbar.classList.toggle('navbar--compact', hasOverflow);
    };

    let resizeTimer = null;
    updateLayout();
    window.addEventListener('resize', () => {
        window.clearTimeout(resizeTimer);
        resizeTimer = window.setTimeout(updateLayout, 80);
    });
    if (document.fonts?.ready) {
        document.fonts.ready.then(updateLayout);
    }
    if ('ResizeObserver' in window) {
        const observer = new ResizeObserver(() => {
            window.clearTimeout(resizeTimer);
            resizeTimer = window.setTimeout(updateLayout, 40);
        });
        observer.observe(navbar);
    }
}

function initOperationHistories() {
    document.querySelectorAll('[data-operation-history]').forEach(history => {
        const toggle = history.querySelector('.plan-goal__history-toggle');
        const content = history.querySelector('.plan-goal__history-content');
        if (!toggle || !content) return;

        const finishTransition = event => {
            if (event.propertyName !== 'height') return;
            const isOpen = toggle.getAttribute('aria-expanded') === 'true';
            if (isOpen) {
                content.style.height = 'auto';
            } else {
                content.hidden = true;
            }
        };

        content.addEventListener('transitionend', finishTransition);
        toggle.addEventListener('click', () => {
            const shouldOpen = toggle.getAttribute('aria-expanded') !== 'true';
            toggle.setAttribute('aria-expanded', String(shouldOpen));
            content.setAttribute('aria-hidden', String(!shouldOpen));

            if (shouldOpen) {
                content.hidden = false;
                content.style.height = '0px';
                history.classList.add('is-open');
                content.getBoundingClientRect();
                window.requestAnimationFrame(() => {
                    content.style.height = `${content.scrollHeight}px`;
                });
                return;
            }

            content.style.height = `${content.getBoundingClientRect().height}px`;
            content.getBoundingClientRect();
            history.classList.remove('is-open');
            window.requestAnimationFrame(() => {
                content.style.height = '0px';
            });
            window.setTimeout(() => {
                if (toggle.getAttribute('aria-expanded') !== 'true') content.hidden = true;
            }, 320);
        });
    });
}

function formatDate(value) {
    if (!value) return '—';
    const [year, month, day] = String(value).split('-');
    if (!year || !month || !day) return value;
    return `${day}.${month}.${year}`;
}

function normalizeDecimalInput(value) {
    if (!value && value !== 0) return '';
    let text = String(value).replace(/\s+/g, '').replace(',', '.');
    const negative = text.startsWith('-');
    if (negative) text = text.slice(1);
    const parts = text.split('.');
    let integer = parts[0].replace(/[^0-9]/g, '');
    let fraction = parts.slice(1).join('').replace(/[^0-9]/g, '');
    if (integer === '') integer = '0';
    if (fraction.length > 2) {
        const parsed = Number(integer + '.' + fraction);
        if (!Number.isNaN(parsed)) {
            text = parsed.toFixed(2);
            if (negative) text = '-' + text;
            return text;
        }
    }
    return (negative ? '-' : '') + integer + (fraction ? '.' + fraction : '');
}

function formatNumberInputValue(value) {
    if (!value && value !== 0) return '';
    let text = String(value).replace(/\s+/g, '');
    const hasTrailingSeparator = /[.,]$/.test(text);
    text = text.replace(/,/g, '.');
    const negative = text.startsWith('-');
    if (negative) text = text.slice(1);
    const parts = text.split('.');
    let integer = parts[0].replace(/[^0-9]/g, '');
    let fraction = parts.slice(1).join('').replace(/[^0-9]/g, '');
    if (integer === '') integer = '0';
    integer = integer.replace(/\B(?=(\d{3})+(?!\d))/g, ' ');
    if (hasTrailingSeparator) {
        return (negative ? '-' : '') + integer + ',';
    }
    return (negative ? '-' : '') + integer + (fraction ? ',' + fraction : '');
}

function parseNumberInputValue(value) {
    const normalized = normalizeDecimalInput(value);
    if (normalized === '') return null;
    const parsed = Number(normalized);
    return Number.isFinite(parsed) ? parsed : null;
}

function setupNumberFormatInputs() {
    document.querySelectorAll('.number-format').forEach(input => {
        input.addEventListener('input', () => {
            const cursorPos = input.selectionStart;
            const before = input.value;
            input.value = formatNumberInputValue(before);
            const diff = input.value.length - before.length;
            if (typeof cursorPos === 'number') {
                input.setSelectionRange(cursorPos + diff, cursorPos + diff);
            }
        });

        input.addEventListener('blur', () => {
            const normalized = normalizeDecimalInput(input.value);
            input.value = formatNumberInputValue(normalized);
        });
    });
}

function setupRequiredFieldIndicators() {
    const requiredFields = document.querySelectorAll('input[required], select[required], textarea[required]');
    const updateIndicator = field => {
        const wrapper = field.closest('.mb-3, .col-md-6, .col-12, .form-group') || field.parentElement;
        const labelIndicator = wrapper?.querySelector('.form-label .required-indicator');
        if (!labelIndicator) return;
        const valid = field.checkValidity() && String(field.value).trim() !== '';
        labelIndicator.textContent = valid ? '✓' : '*';
        labelIndicator.classList.toggle('required-valid', valid);
    };

    requiredFields.forEach(field => {
        updateIndicator(field);
        field.addEventListener('input', () => updateIndicator(field));
        field.addEventListener('change', () => updateIndicator(field));
        field.addEventListener('blur', () => updateIndicator(field));
    });
}

function setupRecurringPaymentToggle() {
    const dateInput = document.getElementById('f_next_payment_date');
    const recurringInput = document.getElementById('f_is_payment_recurring');
    if (!dateInput || !recurringInput) return;

    const syncRecurringState = () => {
        if (!dateInput.value) recurringInput.checked = false;
    };

    dateInput.addEventListener('change', syncRecurringState);
    recurringInput.addEventListener('change', syncRecurringState);
    syncRecurringState();
}

function syncCardLimitHelperVisibility() {
    const debtType = document.getElementById('f_debt_type');
    const helper = document.getElementById('card_limit_helper');
    const helperLabel = document.getElementById('limit_available_label');
    const nextPaymentLabel = document.getElementById('next_payment_date_label');
    const recurringLabel = document.getElementById('recurring_payment_label');
    const availableInput = document.getElementById('f_card_available_amount');
    const firstPaymentGroup = document.getElementById('first_payment_amount_group');
    const firstPaymentInput = document.getElementById('f_first_payment_amount');
    if (!debtType || !helper) return;

    const supportsLimitHelper = ['credit_card', 'split'].includes(debtType.value);
    const isSplit = debtType.value === 'split';
    if (helperLabel) {
        helperLabel.textContent = isSplit
            ? 'Доступно на счёте (₽)'
            : 'Доступно на карте (₽)';
    }
    if (nextPaymentLabel) {
        nextPaymentLabel.textContent = isSplit
            ? 'Дата первого платежа'
            : 'Дата следующего платежа';
    }
    if (recurringLabel) {
        recurringLabel.textContent = isSplit
            ? 'Обновлять каждые 2 недели'
            : 'Обновлять каждый месяц';
    }
    helper.classList.toggle('d-none', !supportsLimitHelper);
    if (!supportsLimitHelper && availableInput) availableInput.value = '';
    if (firstPaymentGroup) firstPaymentGroup.classList.toggle('d-none', !debtType.value || isSplit);
    if (isSplit && firstPaymentInput) firstPaymentInput.value = '';
}

const DEFAULT_PENALTY_RATE = '20';

function syncAccrualSettings({ applyDefaults = false } = {}) {
    const debtType = document.getElementById('f_debt_type');
    const penaltyInput = document.getElementById('f_penalty_rate');
    const graceGroup = document.getElementById('grace_period_group');
    const graceInput = document.getElementById('f_grace_period_end_date');
    const capitalizeGroup = document.getElementById('capitalize_interest_group');
    const capitalizeInput = document.getElementById('f_capitalize_interest');
    if (!debtType || !penaltyInput || !graceGroup || !graceInput || !capitalizeGroup || !capitalizeInput) return;

    const isCard = debtType.value === 'credit_card';
    const isSplit = debtType.value === 'split';
    graceGroup.classList.toggle('d-none', !isCard);
    if (!isCard) graceInput.value = '';
    capitalizeGroup.classList.toggle('d-none', isSplit);
    if (isSplit) capitalizeInput.checked = false;
    // A new debt gets the default penalty; a split has none unless entered.
    if (applyDefaults) penaltyInput.value = isSplit ? '' : DEFAULT_PENALTY_RATE;
}

function setupAccrualSettings() {
    const debtType = document.getElementById('f_debt_type');
    if (!debtType) return;
    debtType.addEventListener('change', () => syncAccrualSettings({ applyDefaults: !currentDebtId }));
    syncAccrualSettings();
}

function setupCardLimitCalculator() {
    const debtType = document.getElementById('f_debt_type');
    const totalInput = document.getElementById('f_total_amount');
    const availableInput = document.getElementById('f_card_available_amount');
    const remainingInput = document.getElementById('f_remaining_amount');
    if (!debtType || !totalInput || !availableInput || !remainingInput) return;

    const recalculateRemaining = () => {
        if (!['credit_card', 'split'].includes(debtType.value) || !availableInput.value.trim()) return;
        const total = parseNumberInputValue(totalInput.value);
        const available = parseNumberInputValue(availableInput.value);
        if (total == null || available == null) return;

        const remaining = Math.max(total - available, 0);
        remainingInput.value = formatNumberInputValue(remaining.toFixed(2));
        remainingInput.dispatchEvent(new Event('input', { bubbles: true }));
        remainingInput.dispatchEvent(new Event('change', { bubbles: true }));
    };

    debtType.addEventListener('change', () => {
        syncCardLimitHelperVisibility();
        recalculateRemaining();
    });
    totalInput.addEventListener('input', recalculateRemaining);
    availableInput.addEventListener('input', recalculateRemaining);
    availableInput.addEventListener('blur', recalculateRemaining);
    syncCardLimitHelperVisibility();
}

function syncConsumerEarlyRepaymentSettings() {
    const debtType = document.getElementById('f_debt_type');
    const settings = document.getElementById('consumer_early_repayment_settings');
    const enabledInput = document.getElementById('f_early_repayment_enabled');
    const amountGroup = document.getElementById('early_repayment_amount_group');
    const amountInput = document.getElementById('f_planned_early_repayment_amount');
    const effectiveAmountGroup = document.getElementById('effective_early_repayment_amount_group');
    const effectiveAmountInput = document.getElementById('f_effective_early_repayment_amount');
    if (!debtType || !settings || !enabledInput || !amountGroup || !amountInput
        || !effectiveAmountGroup || !effectiveAmountInput) return;

    const isConsumerCredit = debtType.value === 'consumer_credit';
    settings.classList.toggle('d-none', !isConsumerCredit);
    if (!isConsumerCredit) {
        enabledInput.checked = false;
        amountInput.value = '';
        effectiveAmountInput.value = '';
    }

    const amountEnabled = isConsumerCredit && enabledInput.checked;
    amountGroup.classList.toggle('d-none', !amountEnabled);
    effectiveAmountGroup.classList.toggle('d-none', !amountEnabled);
    amountInput.disabled = !amountEnabled;
    amountInput.required = amountEnabled;
    recalculateEffectiveEarlyRepaymentAmount();
}

function recalculateEffectiveEarlyRepaymentAmount() {
    const desiredInput = document.getElementById('f_planned_early_repayment_amount');
    const minimumInput = document.getElementById('f_minimum_payment');
    const effectiveInput = document.getElementById('f_effective_early_repayment_amount');
    if (!desiredInput || !minimumInput || !effectiveInput) return;

    const desiredTotal = parseNumberInputValue(desiredInput.value);
    const minimumPayment = parseNumberInputValue(minimumInput.value);
    if (!(desiredTotal > 0)) {
        effectiveInput.value = '';
        return;
    }

    const effectiveAmount = Math.max(desiredTotal - Math.max(minimumPayment, 0), 0);
    effectiveInput.value = formatNumberInputValue(effectiveAmount.toFixed(2));
}

function setupConsumerEarlyRepaymentSettings() {
    const debtType = document.getElementById('f_debt_type');
    const enabledInput = document.getElementById('f_early_repayment_enabled');
    const desiredInput = document.getElementById('f_planned_early_repayment_amount');
    const minimumInput = document.getElementById('f_minimum_payment');
    if (!debtType || !enabledInput || !desiredInput || !minimumInput) return;
    debtType.addEventListener('change', syncConsumerEarlyRepaymentSettings);
    enabledInput.addEventListener('change', syncConsumerEarlyRepaymentSettings);
    desiredInput.addEventListener('input', recalculateEffectiveEarlyRepaymentAmount);
    minimumInput.addEventListener('input', recalculateEffectiveEarlyRepaymentAmount);
    syncConsumerEarlyRepaymentSettings();
}

/**
 * Очищает форму долга
 */
function clearDebtForm() {
    ['f_bank_name','f_product_name','f_total_amount','f_card_available_amount','f_remaining_amount',
     'f_minimum_payment','f_first_payment_amount','f_interest_rate','f_interest_rate_after_change',
     'f_interest_rate_change_date','f_next_payment_date','f_interest_period_start_date',
     'f_planned_early_repayment_amount','f_effective_early_repayment_amount','f_loan_term_months','f_monthly_fee_amount',
     'f_bank_remaining_amount','f_comment'].forEach(id => {
        const el = document.getElementById(id);
        if (el) el.value = '';
    });
    const sel = document.getElementById('f_debt_type');
    if (sel) sel.value = '';
    syncCardLimitHelperVisibility();
    const repaymentType = document.getElementById('f_repayment_type');
    if (repaymentType) repaymentType.value = 'annuity';
    const dayCount = document.getElementById('f_day_count_convention');
    if (dayCount) dayCount.value = 'actual_year';
    const earlyStrategy = document.getElementById('f_early_repayment_strategy');
    if (earlyStrategy) earlyStrategy.value = 'reduce_term';
    const earlyRepaymentEnabled = document.getElementById('f_early_repayment_enabled');
    if (earlyRepaymentEnabled) earlyRepaymentEnabled.checked = false;
    syncConsumerEarlyRepaymentSettings();
    const recurring = document.getElementById('f_is_payment_recurring');
    if (recurring) recurring.checked = false;
    const includePaymentDay = document.getElementById('f_include_payment_day');
    if (includePaymentDay) includePaymentDay.checked = false;
    ['f_penalty_rate', 'f_grace_period_end_date', 'f_accrual_start_date'].forEach(id => {
        const el = document.getElementById(id);
        if (el) el.value = '';
    });
    const capitalizeInterest = document.getElementById('f_capitalize_interest');
    if (capitalizeInterest) capitalizeInterest.checked = false;
    syncAccrualSettings();
    document.getElementById('debtFormError').classList.add('d-none');
    currentDebtId = null;

    const requiredFields = document.querySelectorAll('#debtModal input[required], #debtModal select[required], #debtModal textarea[required]');
    requiredFields.forEach(field => {
        field.dispatchEvent(new Event('input'));
    });
}

// ══════════════════════════════════════════════════════════
// МОДАЛКА: Добавить / редактировать долг
// ══════════════════════════════════════════════════════════

function openAddModal() {
    clearDebtForm();
    syncAccrualSettings({ applyDefaults: true });
    document.getElementById('debtModalTitle').textContent = 'Новый долг';
    document.getElementById('debtSaveBtn').textContent = 'Добавить';
    debtModal.show();
}

function fillDebtSampleData() {
    const sampleDate = new Date();
    sampleDate.setDate(sampleDate.getDate() + 7);
    const formattedDate = sampleDate.toISOString().slice(0, 10);

    const sampleData = {
        f_bank_name: 'Тинькофф',
        f_debt_type: 'credit_card',
        f_product_name: 'Tinkoff Platinum',
        f_total_amount: '125000',
        f_remaining_amount: '83250',
        f_minimum_payment: '2350',
        f_interest_rate: '12.5',
        f_interest_rate_after_change: '',
        f_interest_rate_change_date: '',
        f_next_payment_date: formattedDate,
        f_is_payment_recurring: true,
        f_repayment_type: 'annuity',
        f_day_count_convention: 'actual_year',
        f_early_repayment_strategy: 'reduce_term',
        f_include_payment_day: false,
        f_comment: 'Тестовая запись для проверки отображения всех полей'
    };

    Object.entries(sampleData).forEach(([id, value]) => {
        const el = document.getElementById(id);
        if (!el) return;
        if (el.type === 'checkbox') {
            el.checked = Boolean(value);
            el.dispatchEvent(new Event('change'));
            return;
        }
        el.value = value;
        el.dispatchEvent(new Event('input'));
        el.dispatchEvent(new Event('change'));
    });
}

async function openEditModal(debtId) {
    clearDebtForm();
    currentDebtId = debtId;
    document.getElementById('debtModalTitle').textContent = 'Редактировать долг';
    document.getElementById('debtSaveBtn').innerHTML = '<i class="bi bi-check-lg me-1"></i>Сохранить изменения';

    try {
        const resp = await fetch(`/api/debts/${debtId}`);
        const data = await resp.json();
        if (!data.success) throw new Error(data.error);

        const d = data.debt;
        document.getElementById('f_bank_name').value         = d.bank_name || '';
        document.getElementById('f_debt_type').value         = d.debt_type || '';
        document.getElementById('f_product_name').value      = d.product_name || '';
        document.getElementById('f_total_amount').value      = d.total_amount || '';
        document.getElementById('f_remaining_amount').value  = d.remaining_amount || '';
        document.getElementById('f_minimum_payment').value   = d.minimum_payment || '';
        document.getElementById('f_first_payment_amount').value = d.first_payment_amount || '';
        document.getElementById('f_interest_rate').value     = d.interest_rate || '';
        document.getElementById('f_interest_rate_after_change').value = d.interest_rate_after_change || '';
        document.getElementById('f_interest_rate_change_date').value  = d.interest_rate_change_date || '';
        document.getElementById('f_next_payment_date').value = d.next_payment_date || '';
        document.getElementById('f_is_payment_recurring').checked = Boolean(d.is_payment_recurring);
        document.getElementById('f_repayment_type').value = d.repayment_type || 'annuity';
        document.getElementById('f_day_count_convention').value = d.day_count_convention || 'actual_year';
        document.getElementById('f_include_payment_day').checked = Boolean(d.include_payment_day);
        document.getElementById('f_interest_period_start_date').value = d.interest_period_start_date || '';
        document.getElementById('f_early_repayment_strategy').value = d.early_repayment_strategy || 'reduce_term';
        document.getElementById('f_early_repayment_enabled').checked = Boolean(d.early_repayment_enabled);
        document.getElementById('f_planned_early_repayment_amount').value = d.planned_early_repayment_amount || '';
        document.getElementById('f_loan_term_months').value = d.loan_term_months || '';
        document.getElementById('f_monthly_fee_amount').value = d.monthly_fee_amount || '';
        document.getElementById('f_bank_remaining_amount').value = d.bank_remaining_amount || '';
        document.getElementById('f_comment').value           = d.comment || '';
        document.getElementById('f_penalty_rate').value = d.penalty_rate ?? '';
        document.getElementById('f_capitalize_interest').checked = Boolean(d.capitalize_interest);
        document.getElementById('f_grace_period_end_date').value = d.grace_period_end_date || '';
        document.getElementById('f_accrual_start_date').value = d.accrual_start_date || '';
        syncCardLimitHelperVisibility();
        syncConsumerEarlyRepaymentSettings();
        syncAccrualSettings();

        debtModal.show();
    } catch (err) {
        showToast('Не удалось загрузить данные: ' + err.message, 'danger');
    }
}

/**
 * Сохранить долг (создать или обновить)
 */
async function saveDebt() {
    const errEl = document.getElementById('debtFormError');
    errEl.classList.add('d-none');

    const earlyRepaymentEnabled = document.getElementById('f_early_repayment_enabled').checked;
    const plannedEarlyRepaymentAmount = document.getElementById('f_planned_early_repayment_amount').value;
    const desiredTotalPayment = parseNumberInputValue(plannedEarlyRepaymentAmount);
    const minimumPayment = parseNumberInputValue(document.getElementById('f_minimum_payment').value);
    if (earlyRepaymentEnabled && !(desiredTotalPayment > 0)) {
        errEl.textContent = 'Укажите желаемый платёж всего';
        errEl.classList.remove('d-none');
        return;
    }
    if (earlyRepaymentEnabled && desiredTotalPayment <= minimumPayment) {
        errEl.textContent = 'Желаемый платёж должен быть больше минимального платежа';
        errEl.classList.remove('d-none');
        return;
    }

    const payload = {
        bank_name:         document.getElementById('f_bank_name').value.trim(),
        debt_type:         document.getElementById('f_debt_type').value,
        product_name:      document.getElementById('f_product_name').value.trim(),
        total_amount:      document.getElementById('f_total_amount').value,
        remaining_amount:  document.getElementById('f_remaining_amount').value,
        minimum_payment:   document.getElementById('f_minimum_payment').value || null,
        first_payment_amount: document.getElementById('f_first_payment_amount').value || null,
        interest_rate:     document.getElementById('f_interest_rate').value || null,
        interest_rate_after_change: document.getElementById('f_interest_rate_after_change').value || null,
        interest_rate_change_date: document.getElementById('f_interest_rate_change_date').value || null,
        next_payment_date: document.getElementById('f_next_payment_date').value || null,
        is_payment_recurring: document.getElementById('f_is_payment_recurring').checked,
        repayment_type: document.getElementById('f_repayment_type').value,
        day_count_convention: document.getElementById('f_day_count_convention').value,
        include_payment_day: document.getElementById('f_include_payment_day').checked,
        interest_period_start_date: document.getElementById('f_interest_period_start_date').value || null,
        early_repayment_strategy: document.getElementById('f_early_repayment_strategy').value,
        early_repayment_enabled: earlyRepaymentEnabled,
        planned_early_repayment_amount: plannedEarlyRepaymentAmount || null,
        loan_term_months: document.getElementById('f_loan_term_months').value || null,
        monthly_fee_amount: document.getElementById('f_monthly_fee_amount').value || null,
        bank_remaining_amount: document.getElementById('f_bank_remaining_amount').value || null,
        penalty_rate: document.getElementById('f_penalty_rate').value || null,
        capitalize_interest: document.getElementById('f_capitalize_interest').checked,
        grace_period_end_date: document.getElementById('f_grace_period_end_date').value || null,
        accrual_start_date: document.getElementById('f_accrual_start_date').value || null,
        comment:           document.getElementById('f_comment').value.trim() || null,
    };

    const isEdit = !!currentDebtId;
    const url    = isEdit ? `/api/debts/${currentDebtId}` : '/api/debts';
    const method = isEdit ? 'PUT' : 'POST';

    const btn = document.getElementById('debtSaveBtn');
    btn.disabled = true;
    btn.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span>Сохраняем...';

    try {
        const resp = await jsonFetch(url, {
            method,
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        });
        const data = await resp.json();

        if (data.success) {
            debtModal.hide();
            showToast(isEdit ? 'Карточка обновлена' : 'Карточка добавлена', 'success');
            setTimeout(() => location.reload(), 700);
        } else {
            errEl.textContent = data.error || 'Произошла ошибка';
            errEl.classList.remove('d-none');
        }
    } catch (err) {
        errEl.textContent = 'Ошибка сети: ' + err.message;
        errEl.classList.remove('d-none');
    } finally {
        btn.disabled = false;
        btn.innerHTML = '<i class="bi bi-check-lg me-1"></i>Сохранить';
    }
}

// ══════════════════════════════════════════════════════════
// МОДАЛКА: Внесение платежа
// ══════════════════════════════════════════════════════════

async function openPaymentModal(debtId) {
    currentDebtId = debtId;
    currentMinimumPaymentAmount = null;
    currentMinimumPaymentDate = null;
    currentPlannedEarlyRepaymentAmount = null;
    document.getElementById('paymentFormError').classList.add('d-none');
    document.getElementById('pm_amount').value  = '';
    document.getElementById('pm_principal_amount').value = '';
    document.getElementById('pm_interest_amount').value = '';
    document.getElementById('pm_penalty_amount').value = '';
    document.getElementById('pm_fee_amount').value = '';
    document.getElementById('pm_bank_remaining_after_payment').value = '';
    document.getElementById('pm_comment').value = '';
    document.getElementById('pm_is_early_repayment').checked = false;
    document.getElementById('pm_scheduled_payment_amount').value = '';
    const minimumPaymentButton = document.getElementById('pm_apply_minimum');
    const earlyPaymentButton = document.getElementById('pm_apply_early');
    minimumPaymentButton.disabled = true;
    minimumPaymentButton.classList.remove('payment-preset-shortcut--ready', 'payment-preset-shortcut--selected');
    earlyPaymentButton.disabled = true;
    earlyPaymentButton.classList.add('d-none');
    earlyPaymentButton.classList.remove('payment-preset-shortcut--ready', 'payment-preset-shortcut--selected');
    document.getElementById('pm_min_payment').textContent = '—';
    document.getElementById('pm_early_payment').textContent = '—';

    // Устанавливаем сегодняшнюю дату
    const today = new Date().toISOString().split('T')[0];
    document.getElementById('pm_date').value = today;

    try {
        const resp = await fetch(`/api/debts/${debtId}`);
        const data = await resp.json();
        if (!data.success) throw new Error(data.error);

        const d = data.debt;
        document.getElementById('pm_name').textContent = `${d.bank_name} — ${d.product_name}`;
        document.getElementById('pm_remaining').textContent = formatMoney(d.total_debt ?? d.remaining_amount);
        const minimumPayment = parseNumberInputValue(d.effective_next_payment_amount ?? d.minimum_payment);
        const remainingAmount = parseNumberInputValue(d.total_debt ?? d.remaining_amount);
        currentMinimumPaymentAmount = d.is_first_payment_pending
            ? minimumPayment
            : minimumPayment > 0 && remainingAmount > 0
                ? Math.min(minimumPayment, remainingAmount)
                : minimumPayment;
        const nextDueAmount = Number(d.next_due_amount || 0);
        const restOfCycle = !d.is_overdue && nextDueAmount > 0
            && Math.abs(nextDueAmount - currentMinimumPaymentAmount) >= 0.01;
        if (d.is_overdue && Number(d.amount_to_clear_overdue) > 0) {
            // Overdue first: the preset closes the overdue and its penalty today.
            currentMinimumPaymentAmount = parseNumberInputValue(d.amount_to_clear_overdue);
        } else if (restOfCycle) {
            // What is still due in this cycle: net of payments already made,
            // plus an unpaid penalty that is repaid before the installment.
            currentMinimumPaymentAmount = nextDueAmount;
        }
        currentMinimumPaymentDate = d.is_overdue
            ? today
            : d.effective_next_payment_date || d.next_payment_date || null;
        document.getElementById('pm_required_payment_label').textContent = d.is_overdue
            ? 'Закрыть просрочку'
            : restOfCycle
                ? (Number(d.penalty || 0) > 0 ? 'Платёж с неустойкой' : 'Остаток платежа')
                : d.is_first_payment_pending
                    ? 'Первый платёж'
                    : 'Минимальный платёж';
        const plannedTotalPayment = parseNumberInputValue(d.planned_early_repayment_amount);
        const earlyRepaymentAvailable = (
            d.debt_type === 'consumer_credit'
            && Boolean(d.early_repayment_enabled)
            && plannedTotalPayment > 0
        );
        currentPlannedEarlyRepaymentAmount = earlyRepaymentAvailable && remainingAmount > 0
            ? Math.min(plannedTotalPayment, remainingAmount + currentMinimumPaymentAmount)
            : null;
        document.getElementById('pm_min_payment').textContent = currentMinimumPaymentAmount > 0
            ? formatMoney(currentMinimumPaymentAmount)
            : '—';
        minimumPaymentButton.disabled = !(currentMinimumPaymentAmount > 0);
        minimumPaymentButton.classList.toggle(
            'payment-preset-shortcut--ready',
            currentMinimumPaymentAmount > 0,
        );
        earlyPaymentButton.classList.toggle('d-none', !earlyRepaymentAvailable);
        earlyPaymentButton.disabled = !(currentPlannedEarlyRepaymentAmount > 0);
        earlyPaymentButton.classList.toggle(
            'payment-preset-shortcut--ready',
            currentPlannedEarlyRepaymentAmount > 0,
        );
        document.getElementById('pm_early_payment').textContent = currentPlannedEarlyRepaymentAmount > 0
            ? formatMoney(currentPlannedEarlyRepaymentAmount)
            : '—';

        paymentModal.show();
    } catch (err) {
        showToast('Ошибка загрузки: ' + err.message, 'danger');
    }
}

function applyMinimumPayment() {
    if (!(currentMinimumPaymentAmount > 0)) return;

    applyPaymentPreset({
        amount: currentMinimumPaymentAmount,
        paymentDate: currentMinimumPaymentDate,
        isEarlyRepayment: false,
        scheduledPaymentAmount: null,
        selectedButtonId: 'pm_apply_minimum',
    });
}

function applyPlannedEarlyRepayment() {
    if (!(currentPlannedEarlyRepaymentAmount > 0)) return;

    applyPaymentPreset({
        amount: currentPlannedEarlyRepaymentAmount,
        paymentDate: currentMinimumPaymentDate,
        isEarlyRepayment: true,
        scheduledPaymentAmount: currentMinimumPaymentAmount,
        selectedButtonId: 'pm_apply_early',
    });
}

function applyPaymentPreset({ amount, paymentDate, isEarlyRepayment, scheduledPaymentAmount, selectedButtonId }) {

    const amountInput = document.getElementById('pm_amount');
    const dateInput = document.getElementById('pm_date');
    const earlyRepaymentInput = document.getElementById('pm_is_early_repayment');
    const scheduledPaymentInput = document.getElementById('pm_scheduled_payment_amount');
    const automaticBreakdownInputs = [
        'pm_principal_amount',
        'pm_interest_amount',
        'pm_penalty_amount',
        'pm_fee_amount',
        'pm_bank_remaining_after_payment',
    ];

    amountInput.value = formatNumberInputValue(String(amount));
    amountInput.dispatchEvent(new Event('input', { bubbles: true }));
    amountInput.dispatchEvent(new Event('change', { bubbles: true }));

    if (paymentDate) {
        dateInput.value = paymentDate;
        dateInput.dispatchEvent(new Event('change', { bubbles: true }));
    }

    earlyRepaymentInput.checked = isEarlyRepayment;
    scheduledPaymentInput.value = scheduledPaymentAmount > 0 ? String(scheduledPaymentAmount) : '';
    earlyRepaymentInput.dispatchEvent(new Event('change', { bubbles: true }));
    automaticBreakdownInputs.forEach(id => {
        const input = document.getElementById(id);
        input.value = '';
        input.dispatchEvent(new Event('input', { bubbles: true }));
    });
    document.getElementById('paymentFormError').classList.add('d-none');
    ['pm_apply_minimum', 'pm_apply_early'].forEach(id => {
        document.getElementById(id).classList.toggle(
            'payment-preset-shortcut--selected',
            id === selectedButtonId,
        );
    });
    amountInput.focus();
    amountInput.setSelectionRange(amountInput.value.length, amountInput.value.length);
}

async function submitPayment() {
    const errEl = document.getElementById('paymentFormError');
    errEl.classList.add('d-none');

    const rawAmount = document.getElementById('pm_amount').value;
    const amount = rawAmount.replace(/\s+/g, '').replace(',', '.');
    const principalAmount = document.getElementById('pm_principal_amount').value.replace(/\s+/g, '').replace(',', '.');
    const interestAmount = document.getElementById('pm_interest_amount').value.replace(/\s+/g, '').replace(',', '.');
    const penaltyAmount = document.getElementById('pm_penalty_amount').value.replace(/\s+/g, '').replace(',', '.');
    const feeAmount = document.getElementById('pm_fee_amount').value.replace(/\s+/g, '').replace(',', '.');
    const bankRemainingAfterPayment = document.getElementById('pm_bank_remaining_after_payment').value.replace(/\s+/g, '').replace(',', '.');
    const pmDate = document.getElementById('pm_date').value;
    const comment = document.getElementById('pm_comment').value.trim();
    const isEarlyRepayment = document.getElementById('pm_is_early_repayment').checked;
    const scheduledPaymentAmount = document.getElementById('pm_scheduled_payment_amount').value;
    const parsedAmount = parseFloat(amount);

    if (!amount || Number.isNaN(parsedAmount) || parsedAmount <= 0) {
        errEl.textContent = 'Введите корректную сумму платежа';
        errEl.classList.remove('d-none');
        return;
    }

    try {
        const resp = await jsonFetch(`/api/debts/${currentDebtId}/payments`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                amount,
                principal_amount: principalAmount || null,
                interest_amount: interestAmount || null,
                penalty_amount: penaltyAmount || null,
                fee_amount: feeAmount || null,
                bank_remaining_after_payment: bankRemainingAfterPayment || null,
                payment_date: pmDate,
                comment,
                is_early_repayment: isEarlyRepayment,
                scheduled_payment_amount: scheduledPaymentAmount || null,
            }),
        });
        const data = await resp.json();

        if (data.success) {
            paymentModal.hide();
            const advancedText = data.next_payment_date_advanced && data.debt?.next_payment_date
                ? ` Следующий платеж: ${formatDate(data.debt.next_payment_date)}.`
                : '';
            showToast(`Платеж ${formatMoney(amount)} внесён.${advancedText}`, 'success');

            // Если долг погашен — показываем предложение архивировать
            if (data.debt_cleared) {
                pendingArchiveId = currentDebtId;
                setTimeout(() => archiveConfirmModal.show(), 400);
            } else {
                setTimeout(() => location.reload(), 700);
            }
        } else {
            errEl.textContent = data.error || 'Ошибка при внесении платежа';
            errEl.classList.remove('d-none');
        }
    } catch (err) {
        errEl.textContent = 'Ошибка сети: ' + err.message;
        errEl.classList.remove('d-none');
    }
}

// Обработчик кнопки подтверждения архивации
const confirmArchiveButton = document.getElementById('confirmArchiveBtn');
if (confirmArchiveButton) {
    confirmArchiveButton.addEventListener('click', async () => {
        if (!pendingArchiveId) return;
        archiveConfirmModal?.hide();
        await archiveDebt(pendingArchiveId);
        pendingArchiveId = null;
    });
}

// Если пользователь закрыл модалку без архивации — перезагружаем страницу
if (archiveConfirmEl) {
    archiveConfirmEl.addEventListener('hidden.bs.modal', () => {
        if (!pendingArchiveId) return;
        setTimeout(() => location.reload(), 100);
    });
}

// ══════════════════════════════════════════════════════════
// АРХИВИРОВАНИЕ
// ══════════════════════════════════════════════════════════

async function archiveDebt(debtId) {
    try {
        const resp = await jsonFetch(`/api/debts/${debtId}/archive`, { method: 'POST' });
        const data = await resp.json();
        if (data.success) {
            showToast('Карточка перемещена в архив', 'warning');
            setTimeout(() => location.reload(), 700);
        } else {
            showToast(data.error, 'danger');
        }
    } catch (err) {
        showToast('Ошибка: ' + err.message, 'danger');
    }
}

// ══════════════════════════════════════════════════════════
// ИСТОРИЯ ПЛАТЕЖЕЙ
// ══════════════════════════════════════════════════════════

async function openHistoryModal(debtId, title) {
    document.getElementById('hist_name').textContent = title;
    document.getElementById('historyContent').innerHTML =
        '<div class="history-empty"><div class="spinner-border spinner-border-sm text-muted"></div> Загрузка...</div>';
    historyModal.show();

    try {
        const resp = await fetch(`/api/debts/${debtId}/payments`);
        const data = await resp.json();
        if (!data.success) throw new Error(data.error);

        const payments = data.payments;
        paymentHistoryCache = new Map(payments.map(payment => [String(payment.id), payment]));
        if (payments.length === 0) {
            document.getElementById('historyContent').innerHTML =
                '<div class="history-empty"><i class="bi bi-inbox fs-3 d-block mb-2"></i>Платежей ещё не было</div>';
            return;
        }

        let rows = payments.map(p => {
            const earlyBadge = p.is_early_repayment
                ? `<span class="payment-badge payment-badge--early">${Number(p.scheduled_payment_amount || 0) > 0 ? 'единый' : 'досрочно'}</span>`
                : '';
            const splitParts = [];
            if (Number(p.principal_amount || 0) || Number(p.interest_amount || 0) || Number(p.fee_amount || 0)) {
                splitParts.push(`долг ${formatMoney(p.principal_amount)}`);
                splitParts.push(`проц. ${formatMoney(p.interest_amount)}`);
                if (Number(p.penalty_amount || 0) > 0) splitParts.push(`неуст. ${formatMoney(p.penalty_amount)}`);
                if (Number(p.fee_amount || 0) > 0) splitParts.push(`ком. ${formatMoney(p.fee_amount)}`);
                if (p.breakdown_source === 'manual') splitParts.push('вручную');
            }
            if (p.bank_remaining_after_payment !== null && p.bank_remaining_after_payment !== undefined) {
                const bankDelta = Math.abs(Number(p.bank_remaining_after_payment) - Number(p.remaining_after_payment));
                const acceptButton = bankDelta >= 0.01
                    ? ` <button type="button" class="btn btn-link btn-sm p-0 align-baseline" onclick="acceptBankRemaining(${debtId}, ${p.id})" title="Подобрать разбивку платежа так, чтобы остаток совпал с банком">принять</button>`
                    : '';
                splitParts.push(`банк ${formatMoney(p.bank_remaining_after_payment)}${acceptButton}`);
            }
            if (Number(p.scheduled_payment_amount || 0) > 0) {
                splitParts.push(`обяз. ${formatMoney(p.scheduled_payment_amount)}`);
                splitParts.push(`досрочно ${formatMoney(p.early_repayment_amount)}`);
            }
            const splitInfo = splitParts.length
                ? `<div class="payment-split">${splitParts.join(' · ')}</div>`
                : '';
            return `
                <tr>
                    <td>${p.payment_date}</td>
                    <td>
                        <div class="fw-semibold text-success">+${formatMoney(p.amount)}${earlyBadge}</div>
                        ${splitInfo}
                    </td>
                    <td>${formatMoney(p.remaining_after_payment)}</td>
                    <td class="text-muted">${p.comment || '—'}</td>
                    <td class="payment-history-actions">
                        <button class="history-icon-btn history-icon-btn-edit" onclick="openEditPaymentModal(${debtId}, ${p.id})" title="Редактировать платеж">
                            <i class="bi bi-pencil"></i>
                        </button>
                        <button class="history-icon-btn history-icon-btn-delete" onclick="deletePayment(${debtId}, ${p.id})" title="Удалить платеж">
                            <i class="bi bi-trash"></i>
                        </button>
                    </td>
                </tr>
            `;
        }).join('');

        document.getElementById('historyContent').innerHTML = `
            <div class="table-responsive history-table-wrapper">
                <table class="history-table">
                    <thead>
                        <tr>
                            <th>Дата</th>
                            <th>Сумма</th>
                            <th>Остаток после</th>
                            <th>Комментарий</th>
                            <th></th>
                        </tr>
                    </thead>
                    <tbody>${rows}</tbody>
                </table>
            </div>
        `;
    } catch (err) {
        document.getElementById('historyContent').innerHTML =
            `<div class="history-empty text-danger">Ошибка загрузки: ${err.message}</div>`;
    }
}

function openEditPaymentModal(debtId, paymentId) {
    const payment = paymentHistoryCache.get(String(paymentId));
    if (!payment) {
        showToast('Платеж не найден в истории', 'danger');
        return;
    }

    currentEditPayment = {
        debtId,
        paymentId,
        scheduledPaymentAmount: payment.scheduled_payment_amount || null,
    };
    document.getElementById('editPaymentFormError').classList.add('d-none');
    document.getElementById('ep_amount').value = Number(payment.amount).toFixed(2);
    document.getElementById('ep_principal_amount').value = Number(payment.principal_amount || 0).toFixed(2);
    document.getElementById('ep_interest_amount').value = Number(payment.interest_amount || 0).toFixed(2);
    document.getElementById('ep_penalty_amount').value = Number(payment.penalty_amount || 0).toFixed(2);
    document.getElementById('ep_fee_amount').value = Number(payment.fee_amount || 0).toFixed(2);
    document.getElementById('ep_bank_remaining_after_payment').value = payment.bank_remaining_after_payment !== null && payment.bank_remaining_after_payment !== undefined
        ? Number(payment.bank_remaining_after_payment).toFixed(2)
        : '';
    document.getElementById('ep_date').value = payment.payment_date_iso || dateDisplayToInput(payment.payment_date);
    document.getElementById('ep_comment').value = payment.comment || '';
    document.getElementById('ep_is_early_repayment').checked = Boolean(payment.is_early_repayment);
    document.getElementById('ep_manual_breakdown').checked = payment.breakdown_source === 'manual';
    syncEditBreakdownInputs();

    historyModal.hide();
    editPaymentModal.show();
}

async function submitPaymentEdit() {
    if (!currentEditPayment) return;

    const errEl = document.getElementById('editPaymentFormError');
    errEl.classList.add('d-none');

    const rawAmount = document.getElementById('ep_amount').value;
    const amount = rawAmount.replace(/\s+/g, '').replace(',', '.');
    const principalAmount = document.getElementById('ep_principal_amount').value.replace(/\s+/g, '').replace(',', '.');
    const interestAmount = document.getElementById('ep_interest_amount').value.replace(/\s+/g, '').replace(',', '.');
    const penaltyAmount = document.getElementById('ep_penalty_amount').value.replace(/\s+/g, '').replace(',', '.');
    const feeAmount = document.getElementById('ep_fee_amount').value.replace(/\s+/g, '').replace(',', '.');
    const manualBreakdown = document.getElementById('ep_manual_breakdown').checked;
    const bankRemainingAfterPayment = document.getElementById('ep_bank_remaining_after_payment').value.replace(/\s+/g, '').replace(',', '.');
    const paymentDate = document.getElementById('ep_date').value;
    const comment = document.getElementById('ep_comment').value.trim();
    const isEarlyRepayment = document.getElementById('ep_is_early_repayment').checked;
    const parsedAmount = parseFloat(amount);

    if (!amount || Number.isNaN(parsedAmount) || parsedAmount <= 0) {
        errEl.textContent = 'Введите корректную сумму платежа';
        errEl.classList.remove('d-none');
        return;
    }
    if (!paymentDate) {
        errEl.textContent = 'Укажите дату платежа';
        errEl.classList.remove('d-none');
        return;
    }

    try {
        const resp = await jsonFetch(`/api/debts/${currentEditPayment.debtId}/payments/${currentEditPayment.paymentId}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                amount,
                manual_breakdown: manualBreakdown,
                principal_amount: manualBreakdown ? (principalAmount || null) : null,
                interest_amount: manualBreakdown ? (interestAmount || null) : null,
                penalty_amount: manualBreakdown ? (penaltyAmount || null) : null,
                fee_amount: feeAmount || null,
                bank_remaining_after_payment: bankRemainingAfterPayment || null,
                payment_date: paymentDate,
                comment,
                is_early_repayment: isEarlyRepayment,
                scheduled_payment_amount: currentEditPayment.scheduledPaymentAmount,
            }),
        });
        const data = await resp.json();

        if (!data.success) {
            errEl.textContent = data.error || 'Ошибка при сохранении платежа';
            errEl.classList.remove('d-none');
            return;
        }

        editPaymentModal.hide();
        showToast('Платеж обновлен', 'success');
        setTimeout(() => location.reload(), 650);
    } catch (err) {
        errEl.textContent = 'Ошибка сети: ' + err.message;
        errEl.classList.remove('d-none');
    }
}

function syncEditBreakdownInputs() {
    const manual = document.getElementById('ep_manual_breakdown').checked;
    ['ep_principal_amount', 'ep_interest_amount', 'ep_penalty_amount'].forEach(id => {
        document.getElementById(id).disabled = !manual;
    });
}

async function acceptBankRemaining(debtId, paymentId) {
    if (!confirm('Принять остаток банка? Разбивка платежа станет ручной, следующие платежи будут пересчитаны.')) return;

    try {
        const resp = await jsonFetch(`/api/debts/${debtId}/payments/${paymentId}/accept-bank-remaining`, { method: 'POST' });
        const data = await resp.json();
        if (!data.success) {
            showToast(data.error || 'Не удалось принять остаток банка', 'danger');
            return;
        }
        historyModal.hide();
        showToast('Остаток банка принят', 'success');
        setTimeout(() => location.reload(), 650);
    } catch (err) {
        showToast('Ошибка сети: ' + err.message, 'danger');
    }
}

async function deletePayment(debtId, paymentId) {
    if (!confirm('Удалить платеж? Остаток, проценты и неустойка по долгу будут пересчитаны.')) return;

    try {
        const resp = await jsonFetch(`/api/debts/${debtId}/payments/${paymentId}`, { method: 'DELETE' });
        const data = await resp.json();
        if (!data.success) {
            showToast(data.error || 'Не удалось удалить платеж', 'danger');
            return;
        }
        historyModal.hide();
        showToast('Платеж удален', 'success');
        setTimeout(() => location.reload(), 650);
    } catch (err) {
        showToast('Ошибка сети: ' + err.message, 'danger');
    }
}

function dateDisplayToInput(displayDate) {
    if (!displayDate || !displayDate.includes('.')) return '';
    const [day, month, year] = displayDate.split('.');
    return `${year}-${month}-${day}`;
}

async function openScheduleModal(debtId, title) {
    currentScheduleDebt = { debtId, title };
    document.getElementById('schedule_name').textContent = title;
    document.getElementById('scheduleContent').innerHTML =
        '<div class="history-empty"><div class="spinner-border spinner-border-sm text-muted"></div> Считаем график...</div>';
    scheduleModal.show();

    try {
        const resp = await fetch(`/api/debts/${debtId}/schedule`);
        const data = await resp.json();
        if (!data.success) throw new Error(data.error);

        const schedule = data.schedule;
        if (!schedule.rows.length) {
            document.getElementById('scheduleContent').innerHTML =
                '<div class="history-empty"><i class="bi bi-check2-circle fs-3 d-block mb-2"></i>Долг уже погашен</div>';
            return;
        }

        const isSplitSchedule = schedule.kind === 'split';
        const splitToolbar = isSplitSchedule ? `
            <div class="schedule-toolbar">
                <button type="button" class="history-icon-btn history-icon-btn-edit" onclick="openSplitPurchaseModal(${debtId})" title="Добавить покупку в сплит">
                    <i class="bi bi-plus-lg"></i>
                </button>
                <span>Покупка</span>
            </div>
        ` : '';
        const rows = schedule.rows.map(row => isSplitSchedule ? `
            <tr>
                <td>${row.number}</td>
                <td>${row.payment_date_display}</td>
                <td>
                    <div>${formatMoney(row.payment)}</div>
                    ${splitComponentsHtml(row.components)}
                </td>
                <td>${formatMoney(row.remaining)}</td>
                <td><span class="schedule-status schedule-status--${row.status || 'planned'}">${row.status_label || 'по плану'}</span></td>
            </tr>
        ` : `
            <tr>
                <td>${row.number}</td>
                <td>${row.payment_date_display}${row.status === 'overdue' ? ' <span class="schedule-status schedule-status--overdue">просрочен</span>' : ''}</td>
                <td>
                    <div>${formatMoney(row.payment)}</div>
                    ${Number(row.penalty || 0) > 0 ? `<div class="small text-muted">в т. ч. неустойка ${formatMoney(row.penalty)}</div>` : ''}
                </td>
                <td>${formatMoney(row.interest)}</td>
                <td>${formatMoney(row.principal)}</td>
                <td>${formatMoney(row.fee)}</td>
                <td>${formatMoney(row.remaining)}</td>
                <td>${row.rate}%</td>
            </tr>
        `).join('');

        const summary = isSplitSchedule ? `
            <div>
                <span>Платежей</span>
                <strong>${schedule.months}</strong>
            </div>
            <div>
                <span>Оплачено</span>
                <strong>${formatMoney(schedule.total_paid)}</strong>
            </div>
            <div>
                <span>Осталось</span>
                <strong>${formatMoney(schedule.total_planned)}</strong>
            </div>
            <div>
                <span>Интервал</span>
                <strong>${schedule.interval_label}</strong>
            </div>
        ` : `
            <div>
                <span>Платежей</span>
                <strong>${schedule.months}</strong>
            </div>
            <div>
                <span>Всего платежей</span>
                <strong>${formatMoney(schedule.total_payments)}</strong>
            </div>
            <div>
                <span>Проценты</span>
                <strong>${formatMoney(schedule.total_interest)}</strong>
            </div>
            <div>
                <span>Комиссии</span>
                <strong>${formatMoney(schedule.total_fees)}</strong>
            </div>
        `;

        const tableHead = isSplitSchedule ? `
            <tr>
                <th>#</th>
                <th>Дата списания</th>
                <th>Платеж</th>
                <th>Остаток</th>
                <th>Статус</th>
            </tr>
        ` : `
            <tr>
                <th>#</th>
                <th>Дата</th>
                <th>Платеж</th>
                <th>Проценты</th>
                <th>Тело долга</th>
                <th>Комиссии</th>
                <th>Остаток</th>
                <th>Ставка</th>
            </tr>
        `;

        document.getElementById('scheduleContent').innerHTML = `
            ${splitToolbar}
            <div class="schedule-summary">
                ${summary}
            </div>
            <div class="table-responsive history-table-wrapper schedule-table-wrapper">
                <table class="history-table schedule-table ${isSplitSchedule ? 'schedule-table--split' : ''}">
                    <thead>
                        ${tableHead}
                    </thead>
                    <tbody>${rows}</tbody>
                </table>
            </div>
        `;
    } catch (err) {
        document.getElementById('scheduleContent').innerHTML =
            `<div class="history-empty text-danger">Не удалось построить график: ${err.message}</div>`;
    }
}

function splitComponentsHtml(components) {
    if (!components || components.length <= 1) return '';
    return `<div class="payment-split">${components.map(item => `${escapeHtml(item.title)} ${formatMoney(item.amount)}`).join(' · ')}</div>`;
}

function escapeHtml(value) {
    return String(value ?? '')
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#039;');
}

function openSplitPurchaseModal(debtId) {
    currentSplitPurchaseDebtId = debtId;
    document.getElementById('splitPurchaseFormError').classList.add('d-none');
    document.getElementById('sp_title').value = '';
    document.getElementById('sp_amount').value = '';
    document.getElementById('sp_installments_count').value = '4';
    document.getElementById('sp_purchase_date').value = new Date().toISOString().slice(0, 10);
    splitPurchaseModal.show();
}

async function submitSplitPurchase() {
    if (!currentSplitPurchaseDebtId) return;
    const errEl = document.getElementById('splitPurchaseFormError');
    errEl.classList.add('d-none');

    const payload = {
        title: document.getElementById('sp_title').value.trim() || null,
        amount: document.getElementById('sp_amount').value,
        purchase_date: document.getElementById('sp_purchase_date').value,
        installments_count: document.getElementById('sp_installments_count').value || '4',
    };

    try {
        const resp = await jsonFetch(`/api/debts/${currentSplitPurchaseDebtId}/split-purchases`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        });
        const data = await resp.json();
        if (!data.success) throw new Error(data.error);

        splitPurchaseModal.hide();
        showToast('Покупка добавлена в график сплита', 'success');
        if (currentScheduleDebt) {
            await openScheduleModal(currentScheduleDebt.debtId, currentScheduleDebt.title);
        } else {
            location.reload();
        }
    } catch (err) {
        errEl.textContent = err.message;
        errEl.classList.remove('d-none');
    }
}

// ══════════════════════════════════════════════════════════
// GOOGLE CALENDAR
// ══════════════════════════════════════════════════════════

/**
 * Открывает Google Calendar с предзаполненным событием платежа
 */
function openGoogleCalendar(debtId, bankName, productName, nextPaymentDate, minPayment, remaining) {
    if (!nextPaymentDate) {
        showToast('Дата следующего платежа не указана', 'warning');
        return;
    }

    // Форматируем дату для Google Calendar: YYYYMMDD
    const dateStr = nextPaymentDate.replace(/-/g, '');
    const dateEnd = getNextDay(dateStr);

    const title = encodeURIComponent(`Платеж по ${bankName}: ${productName}`);
    const details = encodeURIComponent(
        `Минимальный платеж: ${formatMoney(minPayment)}\nОстаток долга: ${formatMoney(remaining)}\n\nАвтоматически создано в officium`
    );

    const url = `https://calendar.google.com/calendar/render?action=TEMPLATE`
        + `&text=${title}`
        + `&dates=${dateStr}/${dateEnd}`
        + `&details=${details}`;

    window.open(url, '_blank');
}

/**
 * Возвращает следующий день в формате YYYYMMDD
 */
function getNextDay(dateStr) {
    const y = parseInt(dateStr.slice(0, 4));
    const m = parseInt(dateStr.slice(4, 6)) - 1;
    const d = parseInt(dateStr.slice(6, 8));
    const next = new Date(y, m, d + 1);
    return next.getFullYear().toString()
        + String(next.getMonth() + 1).padStart(2, '0')
        + String(next.getDate()).padStart(2, '0');
}

// ══════════════════════════════════════════════════════════
// ИНИЦИАЛИЗАЦИЯ
// ══════════════════════════════════════════════════════════

document.addEventListener('DOMContentLoaded', () => {
    // Сбросить форму при закрытии модалки долга
    debtModalEl?.addEventListener('hidden.bs.modal', clearDebtForm);

    // Enter в поле суммы платежа — отправить
    const pmAmount = document.getElementById('pm_amount');
    if (pmAmount) {
        pmAmount.addEventListener('keydown', e => {
            if (e.key === 'Enter') submitPayment();
        });
    }
    const epAmount = document.getElementById('ep_amount');
    if (epAmount) {
        epAmount.addEventListener('keydown', e => {
            if (e.key === 'Enter') submitPaymentEdit();
        });
    }

    // Telegram WebView и тема интерфейса
    initTelegramMiniApp();
    initMobileViewport();
    initThemeToggle();
    initImpersonationBanner();
    initCommentTemplates();
    initNavbarLayout();
    initOperationHistories();

    // Форматирование входных сумм
    setupNumberFormatInputs();
    setupRequiredFieldIndicators();
    setupRecurringPaymentToggle();
    setupCardLimitCalculator();
    setupConsumerEarlyRepaymentSettings();
    setupAccrualSettings();

    const requestedDebtId = new URLSearchParams(window.location.search).get('edit_debt');
    if (requestedDebtId && /^\d+$/.test(requestedDebtId)) {
        openEditModal(Number(requestedDebtId));
    }

    // Анимация карточек при загрузке
    const cards = document.querySelectorAll('.debt-card');
    cards.forEach((card, i) => {
        card.style.opacity = '0';
        card.style.transform = 'translateY(16px)';
        setTimeout(() => {
            card.style.transition = 'opacity 0.35s ease, transform 0.35s ease';
            card.style.opacity = '1';
            card.style.transform = '';
        }, 60 + i * 55);
    });
});
