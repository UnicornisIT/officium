from datetime import date
from flask import abort, redirect, render_template, request, url_for
from flask_login import current_user
from app.models import Income
from app.services.goal_cashflow_service import is_goal_income, mark_goal_cashflow_entries
from app.utils import INCOME_CATEGORIES, group_entries_by_month, parse_date, parse_decimal, is_local_test_user
from extensions import db


def init_app(app):
    @app.route('/incomes', methods=['GET', 'POST'])
    def incomes():
        error_message = request.args.get('error')
        success_message = request.args.get('success')
        form_data = request.form if request.method == 'POST' else {}

        if request.method == 'POST':
            if is_local_test_user():
                error_message = 'Локальный тестовый режим: сохранение доходов отключено.'
            else:
                try:
                    amount = parse_decimal(request.form.get('amount'), 'Сумма', required=True)
                    category = request.form.get('category')
                    if category not in [item[0] for item in INCOME_CATEGORIES]:
                        raise ValueError('Выберите корректную категорию дохода')
                    income_date = parse_date(request.form.get('income_date'), 'Дата', required=True)
                    source = str(request.form.get('source', '')).strip() or None
                    comment = str(request.form.get('comment', '')).strip() or None

                    income = Income(
                        user_id=current_user.id,
                        amount=amount,
                        category=category,
                        source=source,
                        income_date=income_date,
                        comment=comment,
                    )
                    db.session.add(income)
                    db.session.commit()
                    return redirect(url_for('incomes', success='Доход сохранён'))
                except ValueError as e:
                    error_message = str(e)
                except Exception as e:
                    db.session.rollback()
                    error_message = 'Ошибка сервера: ' + str(e)

        if is_local_test_user():
            incomes_list = [
                Income(
                    id=1,
                    user_id=0,
                    amount=85000,
                    category='salary',
                    source='Основная работа',
                    income_date=date(date.today().year, date.today().month, 10),
                    comment='Зарплата за текущий месяц',
                ),
                Income(
                    id=2,
                    user_id=0,
                    amount=15000,
                    category='bonus',
                    source='Премия',
                    income_date=date(date.today().year, date.today().month, 5),
                    comment='Премия за выполнение плана',
                ),
            ]
        else:
            incomes_list = Income.query.filter_by(user_id=current_user.id).order_by(Income.income_date.desc()).all()
            mark_goal_cashflow_entries(incomes=incomes_list)
        source_suggestions = _income_source_suggestions(incomes_list)
        groups = group_entries_by_month(incomes_list, 'income_date')
        active_month = date.today().strftime('%Y-%m')
        if groups and active_month not in [group['year_month'] for group in groups]:
            active_month = groups[0]['year_month']
        return render_template('incomes.html', incomes=incomes_list, groups=groups,
                               active_month=active_month, categories=INCOME_CATEGORIES,
                               success_message=success_message, error_message=error_message,
                               edit_income=None, form_data=form_data,
                               source_suggestions=source_suggestions)

    @app.route('/incomes/edit/<int:income_id>', methods=['GET', 'POST'])
    def edit_income(income_id):
        error_message = None
        success_message = request.args.get('success')
        form_data = request.form if request.method == 'POST' else {}
        if is_local_test_user():
            abort(404)
        income = Income.query.filter_by(id=income_id, user_id=current_user.id).first()
        if not income:
            abort(404)
        if is_goal_income(income.id):
            return redirect(url_for(
                'incomes',
                error='Этот доход связан с финансовой целью. Измените операцию в разделе «Цели».',
            ))

        if request.method == 'POST':
            try:
                amount = parse_decimal(request.form.get('amount'), 'Сумма', required=True)
                category = request.form.get('category')
                if category not in [item[0] for item in INCOME_CATEGORIES]:
                    raise ValueError('Выберите корректную категорию дохода')
                income_date = parse_date(request.form.get('income_date'), 'Дата', required=True)
                source = str(request.form.get('source', '')).strip() or None
                comment = str(request.form.get('comment', '')).strip() or None

                income.amount = amount
                income.category = category
                income.source = source
                income.income_date = income_date
                income.comment = comment
                db.session.commit()
                return redirect(url_for('incomes', success='Доход обновлён'))
            except ValueError as e:
                error_message = str(e)
            except Exception as e:
                db.session.rollback()
                error_message = 'Ошибка сервера: ' + str(e)

        incomes_list = Income.query.filter_by(user_id=current_user.id).order_by(Income.income_date.desc()).all()
        mark_goal_cashflow_entries(incomes=incomes_list)
        source_suggestions = _income_source_suggestions(incomes_list)
        groups = group_entries_by_month(incomes_list, 'income_date')
        return render_template('incomes.html', incomes=incomes_list, groups=groups,
                               active_month=date.today().strftime('%Y-%m'), categories=INCOME_CATEGORIES,
                               success_message=success_message, error_message=error_message,
                               edit_income=income, form_data=form_data,
                               source_suggestions=source_suggestions)

    @app.route('/incomes/delete/<int:income_id>', methods=['POST'])
    def delete_income(income_id):
        if is_local_test_user():
            abort(404)
        income = Income.query.filter_by(id=income_id, user_id=current_user.id).first()
        if not income:
            abort(404)
        if is_goal_income(income.id):
            return redirect(url_for(
                'incomes',
                error='Этот доход связан с финансовой целью. Удалите операцию в разделе «Цели».',
            ))
        db.session.delete(income)
        db.session.commit()
        return redirect(url_for('incomes', success='Доход удалён'))


def _income_source_suggestions(incomes_list, limit=12):
    suggestions = []
    seen = set()
    for income in incomes_list:
        source = str(income.source or '').strip()
        if not source:
            continue
        key = source.lower()
        if key in seen:
            continue
        seen.add(key)
        suggestions.append(source)
        if len(suggestions) >= limit:
            break
    return suggestions
