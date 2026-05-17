from datetime import date
from flask import abort, redirect, render_template, request, url_for
from app.models import Expense
from app.utils import EXPENSE_CATEGORIES, PAYMENT_METHODS, group_entries_by_month, parse_date, parse_decimal, is_local_test_user
from flask_login import current_user
from extensions import db
import uuid


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
                    return redirect(url_for('expenses', success='Расход сохранён'))
                except ValueError as e:
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

                expense.amount = amount
                expense.category = category
                expense.title = title
                expense.expense_date = expense_date
                expense.payment_method = payment_method
                expense.comment = comment
                
                # Логика для is_monthly
                if is_monthly and not expense.is_monthly:
                    # Расход становится ежемесячным
                    expense.is_monthly = True
                    if not expense.monthly_group_id:
                        expense.monthly_group_id = str(uuid.uuid4())
                elif not is_monthly and expense.is_monthly:
                    # Расход больше не ежемесячный
                    expense.is_monthly = False
                else:
                    # Просто обновляем флаг
                    expense.is_monthly = is_monthly
                
                db.session.commit()
                return redirect(url_for('expenses', success='Расход обновлён'))
            except ValueError as e:
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
