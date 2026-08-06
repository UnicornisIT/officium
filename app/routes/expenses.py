import json
import os
import re
import secrets
from datetime import date, datetime, timedelta
from decimal import Decimal

from flask import abort, current_app, redirect, render_template, request, url_for
from sqlalchemy import or_
from app.models import Expense
from app.services.bank_statement_import_service import parse_bank_statement
from app.services.expense_title_service import expense_group_key
from app.services.monthly_expenses_service import (
    find_monthly_expense_for_month,
    generate_monthly_expenses_from_start_date,
)
from app.utils import EXPENSE_CATEGORIES, PAYMENT_METHODS, group_entries_by_month, parse_date, parse_decimal, is_local_test_user
from flask_login import current_user
from extensions import db
import uuid


MAX_IMPORT_ROWS = 500
IMPORT_TTL_HOURS = 4


def init_app(app):
    @app.route('/expenses', methods=['GET', 'POST'])
    def expenses():
        error_message = None
        success_message = request.args.get('success')
        form_data = request.form if request.method == 'POST' else {}

        if request.method == 'POST':
            if is_local_test_user():
                error_message = 'Локальный тестовый режим: сохранение расходов отключено.'
            else:
                try:
                    amount = parse_decimal(request.form.get('amount'), 'Сумма', required=True)
                    category = request.form.get('category')
                    if category not in [item[0] for item in EXPENSE_CATEGORIES]:
                        raise ValueError('Выберите корректную категорию расхода')
                    title = str(request.form.get('title', '')).strip()
                    if not title:
                        raise ValueError('Название расхода обязательно')
                    expense_date = parse_date(request.form.get('expense_date'), 'Дата', required=True)
                    payment_method = str(request.form.get('payment_method', '')).strip() or None
                    comment = str(request.form.get('comment', '')).strip() or None
                    is_monthly = request.form.get('is_monthly') == 'on'

                    expense = Expense(
                        user_id=current_user.id,
                        amount=amount,
                        category=category,
                        title=title,
                        expense_date=expense_date,
                        payment_method=payment_method,
                        comment=comment,
                        is_monthly=is_monthly,
                    )
                    
                    # Если это ежемесячный расход, создаём monthly_group_id
                    if is_monthly:
                        expense.monthly_group_id = str(uuid.uuid4())
                    
                    db.session.add(expense)
                    db.session.commit()
                    if expense.is_monthly:
                        generate_monthly_expenses_from_start_date(expense.id)
                    return redirect(url_for('expenses', success='Расход сохранён'))
                except ValueError as e:
                    db.session.rollback()
                    error_message = str(e)
                except Exception as e:
                    db.session.rollback()
                    error_message = 'Ошибка сервера: ' + str(e)

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
        active_month = date.today().strftime('%Y-%m')
        if groups and active_month not in [group['year_month'] for group in groups]:
            active_month = groups[0]['year_month']
        return render_template('expenses.html', expenses=expenses_list, groups=groups,
                               active_month=active_month, categories=EXPENSE_CATEGORIES,
                               payment_methods=PAYMENT_METHODS, success_message=success_message,
                               error_message=error_message, edit_expense=None, form_data=form_data)

    @app.route('/expenses/import', methods=['GET', 'POST'])
    def import_expenses():
        error_message = None
        parse_result = None
        import_token = None

        if is_local_test_user():
            error_message = 'Локальный тестовый режим: импорт выписки отключен.'
        elif request.method == 'POST':
            upload = request.files.get('statement_file')
            if not upload or not upload.filename:
                error_message = 'Выберите файл выписки.'
            else:
                try:
                    file_bytes = upload.read()
                    if not file_bytes:
                        raise ValueError('Файл пустой.')
                    parse_result = parse_bank_statement(file_bytes, upload.filename)
                    if len(parse_result.rows) > MAX_IMPORT_ROWS:
                        raise ValueError(f'В файле найдено больше {MAX_IMPORT_ROWS} расходов. Загрузите выписку за меньший период.')
                    _prepare_import_rows(parse_result.rows)
                    import_token = _save_import_payload(parse_result)
                except ValueError as exc:
                    error_message = str(exc)

        return render_template(
            'expenses_import.html',
            error_message=error_message,
            parse_result=parse_result,
            import_token=import_token,
            categories=EXPENSE_CATEGORIES,
            payment_methods=PAYMENT_METHODS,
        )

    @app.route('/expenses/import/confirm', methods=['POST'])
    def confirm_import_expenses():
        if is_local_test_user():
            abort(404)

        token = request.form.get('import_token', '')
        try:
            payload = _load_import_payload(token)
            rows = payload.get('rows', [])
            row_count = int(request.form.get('row_count', 0))
            if row_count != len(rows):
                raise ValueError('Данные предпросмотра устарели. Загрузите выписку еще раз.')

            created = 0
            updated_monthly = 0
            skipped_unselected = 0

            for index, row in enumerate(rows):
                action = request.form.get(f'action_{index}')
                if action is None:
                    action = 'create' if request.form.get(f'include_{index}') == 'on' else 'skip'
                if action not in {'create', 'skip', 'update_monthly'}:
                    action = 'create'

                if action == 'skip':
                    skipped_unselected += 1
                    continue

                amount = parse_decimal(request.form.get(f'amount_{index}'), 'Сумма', required=True)
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
                comment = str(request.form.get(f'comment_{index}', '')).strip() or None

                if action == 'update_monthly':
                    monthly_expense = _get_import_monthly_match(row)
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

            db.session.commit()
            _delete_import_payload(token)

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
            return render_template(
                'expenses_import.html',
                error_message=str(exc),
                parse_result=None,
                import_token=None,
                categories=EXPENSE_CATEGORIES,
                payment_methods=PAYMENT_METHODS,
            )
        except Exception as exc:
            db.session.rollback()
            return render_template(
                'expenses_import.html',
                error_message='Ошибка импорта: ' + str(exc),
                parse_result=None,
                import_token=None,
                categories=EXPENSE_CATEGORIES,
                payment_methods=PAYMENT_METHODS,
            )

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
                amount = parse_decimal(request.form.get('amount'), 'Сумма', required=True)
                category = request.form.get('category')
                if category not in [item[0] for item in EXPENSE_CATEGORIES]:
                    raise ValueError('Выберите корректную категорию расхода')
                title = str(request.form.get('title', '')).strip()
                if not title:
                    raise ValueError('Название расхода обязательно')
                expense_date = parse_date(request.form.get('expense_date'), 'Дата', required=True)
                payment_method = str(request.form.get('payment_method', '')).strip() or None
                comment = str(request.form.get('comment', '')).strip() or None
                is_monthly = request.form.get('is_monthly') == 'on'

                was_monthly = expense.is_monthly
                monthly_group_id = expense.monthly_group_id

                expense.amount = amount
                expense.category = category
                expense.title = title
                expense.expense_date = expense_date
                expense.payment_method = payment_method
                expense.comment = comment
                
                if is_monthly:
                    expense.is_monthly = True
                    if not expense.monthly_group_id:
                        expense.monthly_group_id = str(uuid.uuid4())
                    expense.generated_for_month = expense_date.strftime('%Y-%m')

                    duplicate = find_monthly_expense_for_month(
                        current_user.id,
                        expense.monthly_group_id,
                        expense.generated_for_month,
                        exclude_expense_id=expense.id,
                    )
                    if duplicate:
                        raise ValueError('Для этого ежемесячного расхода уже есть запись в выбранном месяце.')
                elif was_monthly and monthly_group_id:
                    for group_expense in Expense.query.filter_by(
                        user_id=current_user.id,
                        monthly_group_id=monthly_group_id,
                    ).all():
                        group_expense.is_monthly = False
                else:
                    expense.is_monthly = False
                
                db.session.commit()
                if expense.is_monthly:
                    generate_monthly_expenses_from_start_date(expense.id)
                return redirect(url_for('expenses', success='Расход обновлён'))
            except ValueError as e:
                db.session.rollback()
                error_message = str(e)
            except Exception as e:
                db.session.rollback()
                error_message = 'Ошибка сервера: ' + str(e)

        expenses_list = Expense.query.filter_by(user_id=current_user.id).order_by(Expense.expense_date.desc()).all()
        groups = group_entries_by_month(expenses_list, 'expense_date')
        return render_template('expenses.html', expenses=expenses_list, groups=groups,
                               active_month=date.today().strftime('%Y-%m'), categories=EXPENSE_CATEGORIES,
                               payment_methods=PAYMENT_METHODS, success_message=success_message,
                               error_message=error_message, edit_expense=expense, form_data=form_data)

    @app.route('/expenses/delete/<int:expense_id>', methods=['POST'])
    def delete_expense(expense_id):
        if is_local_test_user():
            abort(404)
        expense = Expense.query.filter_by(id=expense_id, user_id=current_user.id).first()
        if not expense:
            abort(404)
        db.session.delete(expense)
        db.session.commit()
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


def _get_import_monthly_match(row):
    monthly_id = row.get('monthly_match_id')
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


def _import_dir():
    path = os.path.join(current_app.instance_path, 'bank_imports')
    os.makedirs(path, exist_ok=True)
    return path


def _cleanup_old_imports():
    threshold = datetime.utcnow() - timedelta(hours=IMPORT_TTL_HOURS)
    for filename in os.listdir(_import_dir()):
        if not filename.endswith('.json'):
            continue
        path = os.path.join(_import_dir(), filename)
        try:
            modified = datetime.utcfromtimestamp(os.path.getmtime(path))
            if modified < threshold:
                os.remove(path)
        except OSError:
            continue


def _save_import_payload(parse_result):
    _cleanup_old_imports()
    token = secrets.token_urlsafe(24)
    payload = {
        'user_id': current_user.id,
        'created_at': datetime.utcnow().isoformat(),
        'bank': parse_result.bank,
        'rows': [row.to_dict() for row in parse_result.rows],
    }
    path = os.path.join(_import_dir(), f'{token}.json')
    with open(path, 'w', encoding='utf-8') as handle:
        json.dump(payload, handle, ensure_ascii=False)
    return token


def _load_import_payload(token):
    if not token or not re.match(r'^[A-Za-z0-9_-]+$', token):
        raise ValueError('Импорт не найден. Загрузите выписку еще раз.')
    path = os.path.join(_import_dir(), f'{token}.json')
    if not os.path.exists(path):
        raise ValueError('Импорт не найден или устарел. Загрузите выписку еще раз.')
    with open(path, 'r', encoding='utf-8') as handle:
        payload = json.load(handle)
    if payload.get('user_id') != current_user.id:
        raise ValueError('Этот предпросмотр импорта принадлежит другому пользователю.')
    created_at = datetime.fromisoformat(payload.get('created_at'))
    if created_at < datetime.utcnow() - timedelta(hours=IMPORT_TTL_HOURS):
        _delete_import_payload(token)
        raise ValueError('Предпросмотр импорта устарел. Загрузите выписку еще раз.')
    return payload


def _delete_import_payload(token):
    if not token:
        return
    path = os.path.join(_import_dir(), f'{token}.json')
    try:
        os.remove(path)
    except OSError:
        pass
