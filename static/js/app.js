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

// ── Bootstrap объекты ──
const debtModalEl     = document.getElementById('debtModal');
const paymentModalEl  = document.getElementById('paymentModal');
const editPaymentModalEl = document.getElementById('editPaymentModal');
const historyModalEl  = document.getElementById('historyModal');
const scheduleModalEl = document.getElementById('scheduleModal');
const splitPurchaseModalEl = document.getElementById('splitPurchaseModal');
const archiveConfirmEl = document.getElementById('archiveConfirmModal');

const debtModal       = new bootstrap.Modal(debtModalEl);
const paymentModal    = new bootstrap.Modal(paymentModalEl);
const editPaymentModal = new bootstrap.Modal(editPaymentModalEl);
const historyModal    = new bootstrap.Modal(historyModalEl);
const scheduleModal   = new bootstrap.Modal(scheduleModalEl);
const splitPurchaseModal = new bootstrap.Modal(splitPurchaseModalEl);
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

/**
 * Очищает форму долга
 */
function clearDebtForm() {
    ['f_bank_name','f_product_name','f_total_amount','f_card_available_amount','f_remaining_amount',
     'f_minimum_payment','f_interest_rate','f_interest_rate_after_change',
     'f_interest_rate_change_date','f_next_payment_date','f_interest_period_start_date',
     'f_loan_term_months','f_monthly_fee_amount','f_bank_remaining_amount','f_comment'].forEach(id => {
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
    const recurring = document.getElementById('f_is_payment_recurring');
    if (recurring) recurring.checked = false;
    const includePaymentDay = document.getElementById('f_include_payment_day');
    if (includePaymentDay) includePaymentDay.checked = false;
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
        document.getElementById('f_loan_term_months').value = d.loan_term_months || '';
        document.getElementById('f_monthly_fee_amount').value = d.monthly_fee_amount || '';
        document.getElementById('f_bank_remaining_amount').value = d.bank_remaining_amount || '';
        document.getElementById('f_comment').value           = d.comment || '';
        syncCardLimitHelperVisibility();

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
        interest_rate_after_change: document.getElementById('f_interest_rate_after_change').value || null,
        interest_rate_change_date: document.getElementById('f_interest_rate_change_date').value || null,
        next_payment_date: document.getElementById('f_next_payment_date').value || null,
        is_payment_recurring: document.getElementById('f_is_payment_recurring').checked,
        repayment_type: document.getElementById('f_repayment_type').value,
        day_count_convention: document.getElementById('f_day_count_convention').value,
        include_payment_day: document.getElementById('f_include_payment_day').checked,
        interest_period_start_date: document.getElementById('f_interest_period_start_date').value || null,
        early_repayment_strategy: document.getElementById('f_early_repayment_strategy').value,
        loan_term_months: document.getElementById('f_loan_term_months').value || null,
        monthly_fee_amount: document.getElementById('f_monthly_fee_amount').value || null,
        bank_remaining_amount: document.getElementById('f_bank_remaining_amount').value || null,
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
    document.getElementById('pm_principal_amount').value = '';
    document.getElementById('pm_interest_amount').value = '';
    document.getElementById('pm_fee_amount').value = '';
    document.getElementById('pm_bank_remaining_after_payment').value = '';
    document.getElementById('pm_comment').value = '';
    document.getElementById('pm_is_early_repayment').checked = false;

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
    const principalAmount = document.getElementById('pm_principal_amount').value.replace(/\s+/g, '').replace(',', '.');
    const interestAmount = document.getElementById('pm_interest_amount').value.replace(/\s+/g, '').replace(',', '.');
    const feeAmount = document.getElementById('pm_fee_amount').value.replace(/\s+/g, '').replace(',', '.');
    const bankRemainingAfterPayment = document.getElementById('pm_bank_remaining_after_payment').value.replace(/\s+/g, '').replace(',', '.');
    const pmDate = document.getElementById('pm_date').value;
    const comment = document.getElementById('pm_comment').value.trim();
    const isEarlyRepayment = document.getElementById('pm_is_early_repayment').checked;
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
                fee_amount: feeAmount || null,
                bank_remaining_after_payment: bankRemainingAfterPayment || null,
                payment_date: pmDate,
                comment,
                is_early_repayment: isEarlyRepayment,
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
        paymentHistoryCache = new Map(payments.map(payment => [String(payment.id), payment]));
        if (payments.length === 0) {
            document.getElementById('historyContent').innerHTML =
                '<div class="history-empty"><i class="bi bi-inbox fs-3 d-block mb-2"></i>Платежей ещё не было</div>';
            return;
        }

        let rows = payments.map(p => {
            const earlyBadge = p.is_early_repayment
                ? '<span class="payment-badge payment-badge--early">досрочно</span>'
                : '';
            const splitParts = [];
            if (Number(p.principal_amount || 0) || Number(p.interest_amount || 0) || Number(p.fee_amount || 0)) {
                splitParts.push(`долг ${formatMoney(p.principal_amount)}`);
                splitParts.push(`проц. ${formatMoney(p.interest_amount)}`);
                if (Number(p.fee_amount || 0) > 0) splitParts.push(`ком. ${formatMoney(p.fee_amount)}`);
            }
            if (p.bank_remaining_after_payment !== null && p.bank_remaining_after_payment !== undefined) {
                splitParts.push(`банк ${formatMoney(p.bank_remaining_after_payment)}`);
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

    currentEditPayment = { debtId, paymentId };
    document.getElementById('editPaymentFormError').classList.add('d-none');
    document.getElementById('ep_amount').value = Number(payment.amount).toFixed(2);
    document.getElementById('ep_principal_amount').value = Number(payment.principal_amount || 0).toFixed(2);
    document.getElementById('ep_interest_amount').value = Number(payment.interest_amount || 0).toFixed(2);
    document.getElementById('ep_fee_amount').value = Number(payment.fee_amount || 0).toFixed(2);
    document.getElementById('ep_bank_remaining_after_payment').value = payment.bank_remaining_after_payment !== null && payment.bank_remaining_after_payment !== undefined
        ? Number(payment.bank_remaining_after_payment).toFixed(2)
        : '';
    document.getElementById('ep_date').value = payment.payment_date_iso || dateDisplayToInput(payment.payment_date);
    document.getElementById('ep_comment').value = payment.comment || '';
    document.getElementById('ep_is_early_repayment').checked = Boolean(payment.is_early_repayment);

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
    const feeAmount = document.getElementById('ep_fee_amount').value.replace(/\s+/g, '').replace(',', '.');
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
                principal_amount: principalAmount || null,
                interest_amount: interestAmount || null,
                fee_amount: feeAmount || null,
                bank_remaining_after_payment: bankRemainingAfterPayment || null,
                payment_date: paymentDate,
                comment,
                is_early_repayment: isEarlyRepayment,
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
                <td>${row.payment_date_display}</td>
                <td>${formatMoney(row.payment)}</td>
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
    debtModalEl.addEventListener('hidden.bs.modal', clearDebtForm);

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

    // Установка темы интерфейса
    initThemeToggle();

    // Форматирование входных сумм
    setupNumberFormatInputs();
    setupRequiredFieldIndicators();
    setupRecurringPaymentToggle();
    setupCardLimitCalculator();

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
