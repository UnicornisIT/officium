import sys
import traceback
from datetime import date
from decimal import Decimal

from flask import abort, current_app, redirect, render_template, request, url_for
from sqlalchemy import or_
from app.models import Expense
from app.services import import_storage_service as import_storage
from app.services.bank_statement_import_service import parse_bank_statement, too_many_rows_message
from app.services.expense_title_service import expense_group_key
from app.services.history_month_service import month_key, resolve_active_month
from app.services.monthly_expenses_service import (
    find_monthly_expense_for_month,
)
from app.services import expense_service
from app.time_utils import utc_now
from app.utils import EXPENSE_CATEGORIES, PAYMENT_METHODS, group_entries_by_month, parse_date, parse_decimal, is_local_test_user
from flask_login import current_user
from extensions import db


# Used when the configuration has no IMPORT_MAX_ROWS.
MAX_IMPORT_ROWS = 500


def init_app(app):
    @app.route('/expenses', methods=['GET', 'POST'])
    def expenses():
        error_message = request.args.get('error')
        success_message = request.args.get('success')
        form_data = request.form if request.method == 'POST' else {}

        if request.method == 'POST':
            if is_local_test_user():
                error_message = 'Локальный тестовый режим: сохранение расходов отключено.'
            else:
                try:
                    expense = expense_service.create_expense(
                        user_id=current_user.id, **_expense_form_values(),
                    )
                    return redirect(url_for(
                        'expenses',
                        success='Расход сохранён',
                        month=month_key(expense.expense_date),
                    ))
                except ValueError as e:
                    db.session.rollback()
                    error_message = str(e)
                except Exception:
                    db.session.rollback()
                    current_app.logger.exception('Failed to create expense')
                    error_message = 'Не удалось сохранить расход'

        if is_local_test_user():
            expenses_list = [
                Expense(
                    id=1,
                    user_id=0,
                    amount=3500,
                    category='products',
                    title='Продукты',
                    expense_date=date(date.today().year, date.today().month, 12),
                    payment_method='card',
                    comment='Покупка в супермаркете',
                ),
                Expense(
                    id=2,
                    user_id=0,
                    amount=6200,
                    category='transport',
                    title='Транспорт',
                    expense_date=date(date.today().year, date.today().month, 7),
                    payment_method='card',
                    comment='Такси и метро',
                ),
                Expense(
                    id=3,
                    user_id=0,
                    amount=2200,
                    category='subscriptions',
                    title='Подписка',
                    expense_date=date(date.today().year, date.today().month, 3),
                    payment_method='card',
                    comment='Онлайн-сервисы',
                ),
            ]
        else:
            expenses_list = Expense.query.filter_by(user_id=current_user.id).order_by(Expense.expense_date.desc()).all()
        groups = group_entries_by_month(expenses_list, 'expense_date')
        active_month = resolve_active_month(groups, request.args.get('month'))
        return render_template('expenses.html', expenses=expenses_list, groups=groups,
                               active_month=active_month, categories=EXPENSE_CATEGORIES,
                               payment_methods=PAYMENT_METHODS, success_message=success_message,
                               error_message=error_message, edit_expense=None, form_data=form_data)

    @app.route('/expenses/import', methods=['GET', 'POST'])
    def import_expenses():
        error_message = None
        parse_result = None
        import_token = None
        status_code = 200

        if is_local_test_user():
            error_message = 'Локальный тестовый режим: импорт выписки отключен.'
        elif request.method == 'POST':
            upload = request.files.get('statement_file')
            if not upload or not upload.filename:
                error_message = 'Выберите файл выписки.'
                status_code = 400
            else:
                try:
                    with import_storage.parse_slot():
                        file_bytes = upload.read()
                        if not file_bytes:
                            raise ValueError('Файл пустой.')
                        max_rows = current_app.config.get('IMPORT_MAX_ROWS', MAX_IMPORT_ROWS)
                        parse_result = parse_bank_statement(
                            file_bytes,
                            upload.filename,
                            max_rows=max_rows,
                            max_source_rows=current_app.config.get('IMPORT_MAX_SOURCE_ROWS'),
                        )
                        del file_bytes
                        if len(parse_result.rows) > max_rows:
                            raise ValueError(too_many_rows_message(max_rows))
                    _prepare_import_rows(parse_result.rows)
                    import_token = import_storage.save_preview(current_user.id, parse_result.rows)
                except ValueError as exc:
                    parse_result = None
                    error_message = str(exc)
                    status_code = 400
                except import_storage.ImportStorageError as exc:
                    parse_result = None
                    error_message = exc.message
                    status_code = exc.http_status
                except MemoryError:
                    parse_result = None
                    _log_import_failure('Not enough memory to parse a bank statement')
                    error_message = 'Серверу не хватило памяти для разбора файла. Загрузите выписку за меньший период.'
                    status_code = 503
                except Exception:
                    db.session.rollback()
                    parse_result = None
                    _log_import_failure('Failed to prepare a bank statement import preview')
                    error_message = 'Не удалось разобрать выписку. Проверьте файл или загрузите его в другом формате.'
                    status_code = 500

        return render_template(
            'expenses_import.html',
            error_message=error_message,
            parse_result=parse_result,
            import_token=import_token,
            categories=EXPENSE_CATEGORIES,
            payment_methods=PAYMENT_METHODS,
        ), status_code

    @app.route('/expenses/import/confirm', methods=['POST'])
    def confirm_import_expenses():
        if is_local_test_user():
            abort(404)

        token = request.form.get('import_token', '')
        try:
            claim = import_storage.claimed_preview(current_user.id, token)
            with claim as preview:
                created, updated_monthly, skipped_unselected = _apply_confirmed_import(preview)
                db.session.commit()
                claim.complete()

            message = f'Импортировано расходов: {created}.'
            details = []
            if updated_monthly:
                details.append(f'ежемесячных обновлено: {updated_monthly}')
            if skipped_unselected:
                details.append(f'строк не выбрано: {skipped_unselected}')
            if details:
                message += ' ' + ', '.join(details) + '.'
            return redirect(url_for('expenses', success=message))
        except ValueError as exc:
            db.session.rollback()
            return _import_error_page(str(exc), 400)
        except import_storage.ImportStorageError as exc:
            db.session.rollback()
            return _import_error_page(exc.message, exc.http_status)
        except Exception:
            db.session.rollback()
            _log_import_failure('Failed to confirm bank statement import')
            return _import_error_page('Не удалось завершить импорт. Данные не были сохранены.', 500)

    def _import_error_page(message, status_code):
        return render_template(
            'expenses_import.html',
            error_message=message,
            parse_result=None,
            import_token=None,
            categories=EXPENSE_CATEGORIES,
            payment_methods=PAYMENT_METHODS,
        ), status_code

    def _apply_confirmed_import(preview):
        row_count = int(request.form.get('row_count', 0))
        if row_count != preview.row_count:
            raise ValueError('Данные предпросмотра устарели. Загрузите выписку еще раз.')

        created = 0
        updated_monthly = 0
        skipped_unselected = 0

        for index, monthly_match_id in enumerate(preview.monthly_match_ids):
            action = request.form.get(f'action_{index}')
            if action is None:
                action = 'create' if request.form.get(f'include_{index}') == 'on' else 'skip'
            if action not in {'create', 'skip', 'update_monthly'}:
                action = 'create'

            if action == 'skip':
                skipped_unselected += 1
                continue

            amount = parse_decimal(request.form.get(f'amount_{index}'), 'Сумма', required=True)
            if amount <= 0:
                raise ValueError('Сумма импортируемого расхода должна быть больше нуля.')
            expense_date = parse_date(request.form.get(f'expense_date_{index}'), 'Дата', required=True)
            category = request.form.get(f'category_{index}')
            if category not in [item[0] for item in EXPENSE_CATEGORIES]:
                raise ValueError('В одной из строк выбрана некорректная категория.')
            payment_method = request.form.get(f'payment_method_{index}') or 'card'
            if payment_method not in [item[0] for item in PAYMENT_METHODS]:
                payment_method = 'other'
            title = str(request.form.get(f'title_{index}', '')).strip()
            if not title:
                raise ValueError('В одной из строк не заполнено название.')
            if len(title) > 150:
                raise ValueError('Название импортируемого расхода не должно превышать 150 символов.')
            comment = str(request.form.get(f'comment_{index}', '')).strip() or None
            if comment and len(comment) > 2000:
                raise ValueError('Комментарий импортируемого расхода не должен превышать 2000 символов.')

            if action == 'update_monthly':
                monthly_expense = _get_import_monthly_match(monthly_match_id)
                if not monthly_expense:
                    raise ValueError('Ежемесячный расход для одной из строк не найден. Загрузите выписку еще раз.')
                _update_monthly_expense_from_import(
                    monthly_expense,
                    amount=amount,
                    expense_date=expense_date,
                    payment_method=payment_method,
                    comment=comment,
                )
                updated_monthly += 1
                continue

            db.session.add(Expense(
                user_id=current_user.id,
                amount=amount,
                category=category,
                title=title[:150],
                expense_date=expense_date,
                payment_method=payment_method,
                comment=comment,
            ))
            created += 1

        return created, updated_monthly, skipped_unselected

    @app.route('/expenses/edit/<int:expense_id>', methods=['GET', 'POST'])
    def edit_expense(expense_id):
        error_message = None
        success_message = request.args.get('success')
        form_data = request.form if request.method == 'POST' else {}
        if is_local_test_user():
            abort(404)
        expense = Expense.query.filter_by(id=expense_id, user_id=current_user.id).first()
        if not expense:
            abort(404)

        if request.method == 'POST':
            try:
                expense = expense_service.update_expense(
                    user_id=current_user.id, expense_id=expense_id, **_expense_form_values(),
                )
                return redirect(url_for(
                    'expenses',
                    success='Расход обновлён',
                    month=month_key(expense.expense_date),
                ))
            except ValueError as e:
                db.session.rollback()
                error_message = str(e)
            except Exception:
                db.session.rollback()
                current_app.logger.exception('Failed to update expense')
                error_message = 'Не удалось обновить расход'

        expenses_list = Expense.query.filter_by(user_id=current_user.id).order_by(Expense.expense_date.desc()).all()
        groups = group_entries_by_month(expenses_list, 'expense_date')
        # After a failed POST the expense was rolled back, so this is its stored month.
        active_month = resolve_active_month(groups, month_key(expense.expense_date))
        return render_template('expenses.html', expenses=expenses_list, groups=groups,
                               active_month=active_month, categories=EXPENSE_CATEGORIES,
                               payment_methods=PAYMENT_METHODS, success_message=success_message,
                               error_message=error_message, edit_expense=expense, form_data=form_data)

    @app.route('/expenses/delete/<int:expense_id>', methods=['POST'])
    def delete_expense(expense_id):
        if is_local_test_user():
            abort(404)
        expense = Expense.query.filter_by(id=expense_id, user_id=current_user.id).first()
        if not expense:
            abort(404)
        expense_service.delete_expense(user_id=current_user.id, expense_id=expense_id)
        return redirect(url_for('expenses', success='Расход удалён'))


def _prepare_import_rows(rows):
    _mark_import_duplicates(rows)
    _mark_import_monthly_matches(rows)
    for row in rows:
        if getattr(row, 'monthly_match_id', None):
            continue
        row.default_import_action = 'skip' if getattr(row, 'duplicate', False) else 'create'


def _mark_import_duplicates(rows):
    for row in rows:
        row.duplicate = _expense_duplicate_exists(current_user.id, row.expense_date, row.amount, row.title)


def _expense_duplicate_exists(user_id, expense_date, amount, title):
    return Expense.query.filter_by(
        user_id=user_id,
        expense_date=expense_date,
        amount=amount,
        title=title,
    ).first() is not None


def _mark_import_monthly_matches(rows):
    matched_rows = []
    for row in rows:
        match = _find_import_monthly_match(row)
        if not match:
            continue
        _apply_monthly_match(row, match)
        matched_rows.append(row)

    by_expense_id = {}
    for row in matched_rows:
        by_expense_id.setdefault(row.monthly_match_id, []).append(row)

    for monthly_rows in by_expense_id.values():
        chosen = sorted(
            monthly_rows,
            key=lambda item: (
                abs(item.amount - Decimal(str(item.monthly_match_amount or '0'))),
                -int(item.monthly_match_score or 0),
                item.row_number or 0,
            ),
        )[0]
        for row in monthly_rows:
            row.default_import_action = 'update_monthly' if row is chosen else 'create'
            if row is not chosen and row.duplicate:
                row.default_import_action = 'skip'


def _find_import_monthly_match(row):
    candidates = _monthly_expenses_for_import_month(row.expense_date)
    best = None
    best_rank = None
    for expense in candidates:
        if expense.category != row.category:
            continue
        score = _title_match_score(row.title, expense.title)
        if score < 70:
            continue
        amount_diff = abs(row.amount - Decimal(str(expense.amount)))
        date_diff = abs((row.expense_date - expense.expense_date).days)
        rank = (-score, amount_diff, date_diff, expense.id or 0)
        if best_rank is None or rank < best_rank:
            best = expense
            best_rank = rank
            row.monthly_match_score = score
    return best


def _monthly_expenses_for_import_month(expense_date):
    start = date(expense_date.year, expense_date.month, 1)
    end = date(expense_date.year + (1 if expense_date.month == 12 else 0), 1 if expense_date.month == 12 else expense_date.month + 1, 1)
    month = expense_date.strftime('%Y-%m')
    return Expense.query.filter(
        Expense.user_id == current_user.id,
        Expense.is_monthly == True,  # noqa: E712
        Expense.monthly_group_id.isnot(None),
        or_(
            Expense.generated_for_month == month,
            (Expense.expense_date >= start) & (Expense.expense_date < end),
        ),
    ).all()


def _apply_monthly_match(row, expense):
    row.monthly_match_id = expense.id
    row.monthly_match_title = expense.title
    row.monthly_match_amount = str(expense.amount)
    row.monthly_match_category = expense.category


def _title_match_score(left, right):
    left_key = _monthly_title_key(left)
    right_key = _monthly_title_key(right)
    if not left_key or not right_key:
        return 0
    if left_key == right_key:
        return 100
    if len(left_key) >= 4 and len(right_key) >= 4 and (left_key in right_key or right_key in left_key):
        return 88

    left_tokens = {token for token in left_key.split() if len(token) >= 3}
    right_tokens = {token for token in right_key.split() if len(token) >= 3}
    if not left_tokens or not right_tokens:
        return 0

    overlap = left_tokens & right_tokens
    if not overlap:
        return 0
    ratio = len(overlap) / min(len(left_tokens), len(right_tokens))
    if ratio >= 0.67:
        return 78
    if any(len(token) >= 4 for token in overlap):
        return 70
    return 0


def _monthly_title_key(title):
    key = expense_group_key(title)
    aliases = (
        ('билайн', 'beeline'),
        ('bee line', 'beeline'),
        ('beeline', 'beeline'),
        ('мтс', 'mts'),
        ('mts', 'mts'),
        ('мегафон', 'megafon'),
        ('megafon', 'megafon'),
        ('tele2', 'tele2'),
        ('теле2', 'tele2'),
        ('йота', 'yota'),
        ('yota', 'yota'),
        ('яндекс облако', 'yandex oblako'),
        ('yandex oblako', 'yandex oblako'),
        ('яндекс плюс', 'yandex plus'),
        ('yandex plus', 'yandex plus'),
        ('telegram', 'telegram'),
        ('телеграм', 'telegram'),
        ('reg ru', 'reg ru'),
        ('reg.ru', 'reg ru'),
        ('hostkey', 'hostkey'),
    )
    for marker, canonical in aliases:
        if marker in key:
            return canonical
    return key


def _get_import_monthly_match(monthly_id):
    if not monthly_id:
        return None
    try:
        monthly_id = int(monthly_id)
    except (TypeError, ValueError):
        return None
    expense = db.session.get(Expense, monthly_id)
    if not expense or expense.user_id != current_user.id or not expense.is_monthly:
        return None
    return expense


def _update_monthly_expense_from_import(expense, amount, expense_date, payment_method, comment):
    target_month = expense_date.strftime('%Y-%m')
    existing = find_monthly_expense_for_month(
        current_user.id,
        expense.monthly_group_id,
        target_month,
        exclude_expense_id=expense.id,
    )
    if existing:
        raise ValueError('Для одной из строк уже есть другой ежемесячный расход в выбранном месяце.')

    expense.amount = amount
    expense.expense_date = expense_date
    expense.payment_method = payment_method
    expense.comment = comment
    expense.generated_for_month = target_month
    expense.is_monthly = True
    expense.monthly_anchor_day = expense_date.day
    expense.monthly_settings_updated_at = utc_now()


def _log_import_failure(message):
    """Log an unexpected import failure with its stack but without values.

    Exception messages of parsers and drivers may quote statement content
    (merchant names, amounts, card fragments), so only the exception type and
    the code locations are written to the journal.
    """
    exc_type, exc, tb = sys.exc_info()
    frames = ''.join(traceback.format_list(traceback.extract_tb(tb)))
    current_app.logger.error(
        '%s: %s (message withheld: it may contain statement data)\nTraceback (most recent call last):\n%s',
        message,
        exc_type.__name__ if exc_type else 'unknown',
        frames.rstrip(),
    )


def _expense_form_values():
    return dict(
        amount=request.form.get('amount'), category=request.form.get('category'),
        title=request.form.get('title', ''), expense_date=request.form.get('expense_date'),
        payment_method=request.form.get('payment_method'), comment=request.form.get('comment'),
        is_monthly=request.form.get('is_monthly') == 'on',
    )
