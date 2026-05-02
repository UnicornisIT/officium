/* ============================================================
   DEBT TRACKER — Основной JavaScript
   Все взаимодействия: модалки, AJAX, платежи, уведомления
============================================================ */

// ── Глобальные переменные ──
let currentDebtId = null;       // ID долга для операций
let pendingArchiveId = null;    // ID долга, ожидающего архивации

// ── Bootstrap объекты ──
const debtModalEl     = document.getElementById('debtModal');
const paymentModalEl  = document.getElementById('paymentModal');
const historyModalEl  = document.getElementById('historyModal');
const archiveConfirmEl = document.getElementById('archiveConfirmModal');

const debtModal       = new bootstrap.Modal(debtModalEl);
const paymentModal    = new bootstrap.Modal(paymentModalEl);
const historyModal    = new bootstrap.Modal(historyModalEl);
const archiveConfirmModal = new bootstrap.Modal(archiveConfirmEl);

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
    localStorage.setItem('debtManagerTheme', theme);

    const btn = document.getElementById('themeToggleBtn');
    if (!btn) return;

    if (theme === 'dark') {
        btn.innerHTML = '<i class="bi bi-sun-fill"></i> Светлая тема';
        btn.title = 'Переключиться в светлую тему';
    } else {
        btn.innerHTML = '<i class="bi bi-moon-fill"></i> Тёмная тема';
        btn.title = 'Переключиться в тёмную тему';
    }
}

function toggleTheme() {
    const current = document.documentElement.getAttribute('data-theme') || 'dark';
    applyTheme(current === 'dark' ? 'light' : 'dark');
}

function initThemeToggle() {
    const storedTheme = localStorage.getItem('debtManagerTheme') || 'dark';
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
    return fetch(url, { ...options, headers });
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

/**
 * Очищает форму долга
 */
function clearDebtForm() {
    ['f_bank_name','f_product_name','f_total_amount','f_remaining_amount',
     'f_minimum_payment','f_interest_rate','f_next_payment_date','f_comment'].forEach(id => {
        const el = document.getElementById(id);
        if (el) el.value = '';
    });
    const sel = document.getElementById('f_debt_type');
    if (sel) sel.value = '';
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
        f_next_payment_date: formattedDate,
        f_comment: 'Тестовая запись для проверки отображения всех полей'
    };

    Object.entries(sampleData).forEach(([id, value]) => {
        const el = document.getElementById(id);
        if (!el) return;
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
        document.getElementById('f_interest_rate').value     = d.interest_rate || '';
        document.getElementById('f_next_payment_date').value = d.next_payment_date || '';
        document.getElementById('f_comment').value           = d.comment || '';

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

    const payload = {
        bank_name:         document.getElementById('f_bank_name').value.trim(),
        debt_type:         document.getElementById('f_debt_type').value,
        product_name:      document.getElementById('f_product_name').value.trim(),
        total_amount:      document.getElementById('f_total_amount').value,
        remaining_amount:  document.getElementById('f_remaining_amount').value,
        minimum_payment:   document.getElementById('f_minimum_payment').value || null,
        interest_rate:     document.getElementById('f_interest_rate').value || null,
        next_payment_date: document.getElementById('f_next_payment_date').value || null,
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
    document.getElementById('paymentFormError').classList.add('d-none');
    document.getElementById('pm_amount').value  = '';
    document.getElementById('pm_comment').value = '';

    // Устанавливаем сегодняшнюю дату
    const today = new Date().toISOString().split('T')[0];
    document.getElementById('pm_date').value = today;

    try {
        const resp = await fetch(`/api/debts/${debtId}`);
        const data = await resp.json();
        if (!data.success) throw new Error(data.error);

        const d = data.debt;
        document.getElementById('pm_name').textContent = `${d.bank_name} — ${d.product_name}`;
        document.getElementById('pm_remaining').textContent = formatMoney(d.remaining_amount);
        document.getElementById('pm_min_payment').textContent = d.minimum_payment ? formatMoney(d.minimum_payment) : '—';

        paymentModal.show();
    } catch (err) {
        showToast('Ошибка загрузки: ' + err.message, 'danger');
    }
}

async function submitPayment() {
    const errEl = document.getElementById('paymentFormError');
    errEl.classList.add('d-none');

    const rawAmount = document.getElementById('pm_amount').value;
    const amount = rawAmount.replace(/\s+/g, '').replace(',', '.');
    const pmDate = document.getElementById('pm_date').value;
    const comment = document.getElementById('pm_comment').value.trim();
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
            body: JSON.stringify({ amount, payment_date: pmDate, comment }),
        });
        const data = await resp.json();

        if (data.success) {
            paymentModal.hide();
            showToast(`Платеж ${formatMoney(amount)} внесён`, 'success');

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
document.getElementById('confirmArchiveBtn').addEventListener('click', async () => {
    if (!pendingArchiveId) return;
    archiveConfirmModal.hide();
    await archiveDebt(pendingArchiveId);
    pendingArchiveId = null;
});

// Если пользователь закрыл модалку без архивации — перезагружаем страницу
archiveConfirmEl.addEventListener('hidden.bs.modal', () => {
    if (!pendingArchiveId) return;
    setTimeout(() => location.reload(), 100);
});

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
        if (payments.length === 0) {
            document.getElementById('historyContent').innerHTML =
                '<div class="history-empty"><i class="bi bi-inbox fs-3 d-block mb-2"></i>Платежей ещё не было</div>';
            return;
        }

        let rows = payments.map(p => `
            <tr>
                <td>${p.payment_date}</td>
                <td class="fw-semibold text-success">+${formatMoney(p.amount)}</td>
                <td>${formatMoney(p.remaining_after_payment)}</td>
                <td class="text-muted">${p.comment || '—'}</td>
            </tr>
        `).join('');

        document.getElementById('historyContent').innerHTML = `
            <div class="table-responsive history-table-wrapper">
                <table class="history-table">
                    <thead>
                        <tr>
                            <th>Дата</th>
                            <th>Сумма</th>
                            <th>Остаток после</th>
                            <th>Комментарий</th>
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
        `Минимальный платеж: ${formatMoney(minPayment)}\nОстаток долга: ${formatMoney(remaining)}\n\nАвтоматически создано в ДолгТрекере`
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
    debtModalEl.addEventListener('hidden.bs.modal', clearDebtForm);

    // Enter в поле суммы платежа — отправить
    const pmAmount = document.getElementById('pm_amount');
    if (pmAmount) {
        pmAmount.addEventListener('keydown', e => {
            if (e.key === 'Enter') submitPayment();
        });
    }

    // Установка темы интерфейса
    initThemeToggle();

    // Форматирование входных сумм
    setupNumberFormatInputs();
    setupRequiredFieldIndicators();

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
