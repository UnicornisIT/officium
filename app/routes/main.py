from datetime import date, datetime, timedelta
from decimal import Decimal
from io import StringIO
import csv
from flask import abort, current_app, flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user
from extensions import db
from app.models import (
    Debt,
    EmergencyFundTransaction,
    Expense,
    FinancialGoal,
    FinancialGoalTransaction,
    FinancialPlanPreference,
    Income,
    Payment,
)
from app.services.finance_summary_service import get_finance_summary
from app.services.goal_cashflow_service import create_goal_cashflow_entry, delete_goal_cashflow_entries
from app.services.financial_plan_service import (
    build_financial_plan,
    get_emergency_fund_balance,
    get_financial_goal_balance,
    get_financial_plan_preference,
)
from app.services.debt_interest_service import calculate_overdue_interest
from app.services.debt_service import get_user_debt
from app.utils import get_setting, income_source_suggestions, parse_date, parse_decimal


def is_local_test_user():
    return getattr(current_user, 'is_local_test_user', False)


def _financial_plan_redirect(anchor=None):
    year = request.args.get('year', type=int)
    month = request.args.get('month', type=int)
    location = url_for(
        'financial_plan',
        **({'year': year, 'month': month} if year and 1 <= month <= 12 else {}),
    )
    if anchor:
        location += f'#{anchor}'
    return redirect(location)


def _financial_goal_form_values(form):
    name = str(form.get('name', '')).strip()
    if not name:
        raise ValueError('Укажите название цели.')
    if len(name) > 120:
        raise ValueError('Название цели не должно превышать 120 символов.')
    target_amount = parse_decimal(form.get('target_amount'), 'Целевая сумма')
    monthly_contribution = parse_decimal(form.get('monthly_contribution'), 'Сумма в месяц')
    if target_amount <= 0:
        raise ValueError('Целевая сумма должна быть больше нуля.')
    if monthly_contribution < 0:
        raise ValueError('Сумма в месяц не может быть отрицательной.')
    note = str(form.get('note', '')).strip() or None
    if note and len(note) > 500:
        raise ValueError('Заметка не должна превышать 500 символов.')
    return {
        'name': name,
        'target_amount': target_amount,
        'monthly_contribution': monthly_contribution,
        'target_date': parse_date(form.get('target_date'), 'Срок цели'),
        'note': note,
    }


def _normalize_financial_goal_priorities(user_id):
    goals = (
        FinancialGoal.query
        .filter_by(user_id=user_id)
        .order_by(FinancialGoal.priority.asc(), FinancialGoal.id.asc())
        .all()
    )
    for priority, goal in enumerate(goals, start=2):
        goal.priority = priority


def init_app(app):
    @app.route('/')
    def index():
        if is_local_test_user():
            summary = get_finance_summary(None)
        else:
            summary = get_finance_summary(current_user.id)
        total_incomes = summary['total_incomes']
        total_expenses = summary['total_expenses']
        total_payments = summary['total_payments']
        free_balance = summary['free_balance']
        days_left = summary['days_left']
        total_remaining = summary['total_remaining']
        total_original = summary['total_original']
        nearest_debt = summary['nearest_debt']
        overdue_count = summary['overdue_count']
        today = summary['today']
        month_start = summary['month_start']
        active_debts = summary['active_debts']

        active_debts = sorted(
            active_debts,
            key=lambda d: d.effective_next_payment_date(today) or date.max,
        )
        upcoming = [d for d in active_debts if d.effective_next_payment_date(today) and d.effective_next_payment_date(today) >= today]
        nearest_debt = upcoming[0] if upcoming else None
        overdue_count = len([d for d in active_debts if d.effective_next_payment_date(today) and d.effective_next_payment_date(today) < today])

        daily_budget = free_balance / days_left if days_left > 0 else 0
        return render_template('index.html',
            debts=active_debts,
            total_remaining=total_remaining,
            total_original=total_original,
            active_count=len(active_debts),
            nearest_debt=nearest_debt,
            overdue_count=overdue_count,
            today=today,
            month_start=month_start,
            total_incomes=total_incomes,
            total_expenses=total_expenses,
            total_payments=total_payments,
            free_balance=free_balance,
            days_left=days_left,
            daily_budget=daily_budget,
        )

    @app.route('/finance')
    def finance():
        selected_year = request.args.get('year', type=int)
        selected_month = request.args.get('month', type=int)
        if is_local_test_user():
            summary = get_finance_summary(None, selected_year, selected_month)
        else:
            summary = get_finance_summary(current_user.id, selected_year, selected_month)

        today = date.today()
        year_options = [today.year - i for i in range(0, 5)]
        if summary['selected_year'] not in year_options:
            year_options.append(summary['selected_year'])
            year_options.sort(reverse=True)

        month_names = [
            'Январь', 'Февраль', 'Март', 'Апрель', 'Май', 'Июнь',
            'Июль', 'Август', 'Сентябрь', 'Октябрь', 'Ноябрь', 'Декабрь'
        ]
        month_options = list(enumerate(month_names, start=1))

        return render_template('finance.html',
            active_count=len(summary['active_debts']),
            total_remaining=summary['total_remaining'],
            total_original=summary['total_original'],
            nearest_debt=summary['nearest_debt'],
            overdue_count=summary['overdue_count'],
            today=summary['today'],
            month_start=summary['month_start'],
            total_incomes=summary['total_incomes'],
            total_expenses=summary['total_expenses'],
            total_payments=summary['total_payments'],
            free_balance=summary['free_balance'],
            days_left=summary['days_left'],
            daily_budget=(summary['free_balance'] / summary['days_left'] if summary['days_left'] > 0 else 0),
            archived_count=summary['archived_count'],
            total_debts=summary['total_debts'],
            mortgage_debts=summary['mortgage_debts'],
            mortgage_count=summary['mortgage_count'],
            total_mortgage_remaining=summary['total_mortgage_remaining'],
            total_mortgage_interest=summary['total_mortgage_interest'],
            incomes_this_month=summary['incomes_this_month'],
            expenses_this_month=summary['expenses_this_month'],
            payments_this_month=summary['payments_this_month'],
            cashflow=summary['cashflow'],
            expense_category_breakdown=summary['expense_category_breakdown'],
            expense_title_breakdown=summary['expense_title_breakdown'],
            payment_method_breakdown=summary['payment_method_breakdown'],
            largest_expenses=summary['largest_expenses'],
            recurring_expenses_this_month=summary['recurring_expenses_this_month'],
            top_spending_days=summary['top_spending_days'],
            selected_year=summary['selected_year'],
            selected_month=summary['selected_month'],
            year_options=year_options,
            month_options=month_options,
        )

    @app.route('/financial-plan', methods=['GET', 'POST'])
    def financial_plan():
        local_test_user = is_local_test_user()
        user_id = None if local_test_user else current_user.id
        preference = get_financial_plan_preference(user_id)
        selected_year = request.args.get('year', type=int)
        selected_month = request.args.get('month', type=int)

        if request.method == 'POST':
            if local_test_user:
                flash('Настройки демо-плана не сохраняются.', 'info')
                return _financial_plan_redirect()
            try:
                strategy = request.form.get('strategy', 'balanced')
                if strategy not in ('safe', 'balanced', 'aggressive'):
                    raise ValueError('Выберите корректную стратегию.')

                if preference is None:
                    preference = FinancialPlanPreference(user_id=user_id)
                    db.session.add(preference)

                preference.living_minimum = parse_decimal(
                    request.form.get('living_minimum'),
                    'Минимум на жизнь',
                )
                preference.strategy = strategy
                db.session.commit()
                flash('Финансовый план пересчитан.', 'success')
                return _financial_plan_redirect()
            except ValueError as exc:
                db.session.rollback()
                flash(str(exc), 'danger')
            except Exception:
                db.session.rollback()
                current_app.logger.exception('Failed to save financial plan preferences')
                flash('Не удалось сохранить настройки финансового плана.', 'danger')

        plan = build_financial_plan(
            user_id,
            preference=preference,
            year=selected_year,
            month=selected_month,
        )
        month_options = (
            (1, 'Январь'), (2, 'Февраль'), (3, 'Март'), (4, 'Апрель'),
            (5, 'Май'), (6, 'Июнь'), (7, 'Июль'), (8, 'Август'),
            (9, 'Сентябрь'), (10, 'Октябрь'), (11, 'Ноябрь'), (12, 'Декабрь'),
        )
        year_options = sorted(
            set(range(date.today().year - 4, date.today().year + 3)) | {plan['period']['year']},
            reverse=True,
        )
        if local_test_user:
            source_suggestions = []
        else:
            previous_incomes = (
                Income.query
                .filter_by(user_id=user_id)
                .order_by(Income.income_date.desc(), Income.id.desc())
                .all()
            )
            source_suggestions = income_source_suggestions(previous_incomes)
        return render_template(
            'financial_plan.html',
            plan=plan,
            month_options=month_options,
            year_options=year_options,
            source_suggestions=source_suggestions,
        )

    @app.route('/financial-plan/salary-income', methods=['POST'])
    def add_salary_income():
        if is_local_test_user():
            flash('Зарплатные поступления в демо-режиме не сохраняются.', 'info')
            return _financial_plan_redirect('salary-income')

        try:
            amount = parse_decimal(request.form.get('amount'), 'Сумма')
            if amount <= 0:
                raise ValueError('Сумма поступления должна быть больше нуля.')

            category = request.form.get('category', 'salary')
            if category not in ('salary', 'advance'):
                raise ValueError('Выберите корректный вид зарплатного поступления.')

            income_date = parse_date(
                request.form.get('income_date'),
                'Дата поступления',
                required=True,
            )
            source = str(request.form.get('source', '')).strip() or None
            comment = str(request.form.get('comment', '')).strip() or None
            if source and len(source) > 150:
                raise ValueError('Источник не должен превышать 150 символов.')
            if comment and len(comment) > 1000:
                raise ValueError('Комментарий не должен превышать 1000 символов.')

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
            flash(
                'Зарплатное поступление добавлено в доходы. Рекомендации пересчитаны.',
                'success',
            )
            location = url_for(
                'financial_plan',
                year=income_date.year,
                month=income_date.month,
            )
            return redirect(f'{location}#income-allocation-{income.id}')
        except ValueError as exc:
            db.session.rollback()
            flash(str(exc), 'danger')
        except Exception:
            db.session.rollback()
            current_app.logger.exception('Failed to save salary income allocation')
            flash('Не удалось сохранить зарплатное поступление.', 'danger')
        return _financial_plan_redirect('salary-income')

    @app.route('/financial-plan/emergency-fund', methods=['POST'])
    def add_emergency_fund_transaction():
        if is_local_test_user():
            flash('Операции демо-подушки не сохраняются.', 'info')
            return _financial_plan_redirect('goals')
        try:
            transaction_type = request.form.get('transaction_type', 'deposit')
            if transaction_type not in ('deposit', 'withdrawal'):
                raise ValueError('Выберите корректный тип операции.')

            amount = parse_decimal(request.form.get('amount'), 'Сумма')
            if amount <= 0:
                raise ValueError('Сумма операции должна быть больше нуля.')
            if transaction_type == 'withdrawal' and amount > get_emergency_fund_balance(current_user.id):
                raise ValueError('Нельзя снять больше, чем сейчас накоплено в подушке.')

            transaction_date = parse_date(
                request.form.get('transaction_date'),
                'Дата операции',
                required=True,
            )
            comment = str(request.form.get('comment', '')).strip() or None
            if comment and len(comment) > 255:
                raise ValueError('Комментарий не должен превышать 255 символов.')

            cashflow_ids = create_goal_cashflow_entry(
                current_user.id,
                'Финансовая подушка',
                transaction_type,
                amount,
                transaction_date,
                comment,
            )
            db.session.add(EmergencyFundTransaction(
                user_id=current_user.id,
                transaction_type=transaction_type,
                amount=amount,
                transaction_date=transaction_date,
                comment=comment,
                **cashflow_ids,
            ))
            db.session.commit()
            action_label = 'Пополнение' if transaction_type == 'deposit' else 'Снятие'
            flash(f'{action_label} финансовой подушки учтено.', 'success')
        except ValueError as exc:
            db.session.rollback()
            flash(str(exc), 'danger')
        except Exception:
            db.session.rollback()
            current_app.logger.exception('Failed to save emergency fund transaction')
            flash('Не удалось сохранить операцию финансовой подушки.', 'danger')
        return _financial_plan_redirect('goals')

    @app.route('/financial-plan/emergency-fund/<int:transaction_id>/delete', methods=['POST'])
    def delete_emergency_fund_transaction(transaction_id):
        if is_local_test_user():
            abort(404)
        transaction = EmergencyFundTransaction.query.filter_by(
            id=transaction_id,
            user_id=current_user.id,
        ).first()
        if not transaction:
            abort(404)
        current_balance = get_emergency_fund_balance(current_user.id)
        if transaction.transaction_type == 'deposit' and transaction.amount > current_balance:
            flash('Сначала удалите связанные снятия: без этого остаток станет отрицательным.', 'danger')
            return _financial_plan_redirect('goals')
        delete_goal_cashflow_entries([transaction])
        db.session.delete(transaction)
        db.session.commit()
        flash('Операция финансовой подушки удалена.', 'success')
        return _financial_plan_redirect('goals')

    @app.route('/financial-plan/goals/emergency/edit', methods=['POST'])
    def edit_emergency_goal():
        if is_local_test_user():
            flash('Цели демо-плана не изменяются.', 'info')
            return _financial_plan_redirect('goals')
        try:
            target_mode = request.form.get('target_mode', 'fixed')
            if target_mode not in ('fixed', 'one_month', 'three_months'):
                raise ValueError('Выберите корректный способ расчета подушки.')
            preference = get_financial_plan_preference(current_user.id)
            if preference is None:
                preference = FinancialPlanPreference(user_id=current_user.id)
                db.session.add(preference)
            target_amount = parse_decimal(request.form.get('target_amount'), 'Целевая сумма')
            monthly_contribution = parse_decimal(request.form.get('monthly_contribution'), 'Сумма в месяц')
            if target_amount <= 0:
                raise ValueError('Целевая сумма должна быть больше нуля.')
            if monthly_contribution < 0:
                raise ValueError('Сумма в месяц не может быть отрицательной.')
            preference.emergency_fund_target_mode = target_mode
            preference.emergency_fund_target_amount = target_amount
            preference.desired_monthly_savings = monthly_contribution
            db.session.commit()
            flash('Параметры финансовой подушки обновлены.', 'success')
        except ValueError as exc:
            db.session.rollback()
            flash(str(exc), 'danger')
        except Exception:
            db.session.rollback()
            current_app.logger.exception('Failed to update emergency fund preferences')
            flash('Не удалось обновить финансовую подушку.', 'danger')
        return _financial_plan_redirect('goals')

    @app.route('/financial-plan/goals', methods=['POST'])
    def create_financial_goal():
        if is_local_test_user():
            flash('Цели демо-плана не изменяются.', 'info')
            return _financial_plan_redirect('goals')
        try:
            values = _financial_goal_form_values(request.form)
            highest_priority = (
                db.session.query(db.func.max(FinancialGoal.priority))
                .filter(FinancialGoal.user_id == current_user.id)
                .scalar()
            )
            db.session.add(FinancialGoal(
                user_id=current_user.id,
                priority=max(highest_priority or 1, 1) + 1,
                **values,
            ))
            db.session.commit()
            flash(f'Цель «{values["name"]}» создана.', 'success')
        except ValueError as exc:
            db.session.rollback()
            flash(str(exc), 'danger')
        except Exception:
            db.session.rollback()
            current_app.logger.exception('Failed to create financial goal')
            flash('Не удалось создать цель.', 'danger')
        return _financial_plan_redirect('goals')

    @app.route('/financial-plan/goals/<int:goal_id>/edit', methods=['POST'])
    def edit_financial_goal(goal_id):
        if is_local_test_user():
            abort(404)
        goal = FinancialGoal.query.filter_by(id=goal_id, user_id=current_user.id).first()
        if goal is None:
            abort(404)
        try:
            values = _financial_goal_form_values(request.form)
            for field, value in values.items():
                setattr(goal, field, value)
            db.session.commit()
            flash(f'Цель «{goal.name}» обновлена.', 'success')
        except ValueError as exc:
            db.session.rollback()
            flash(str(exc), 'danger')
        except Exception:
            db.session.rollback()
            current_app.logger.exception('Failed to update financial goal')
            flash('Не удалось обновить цель.', 'danger')
        return _financial_plan_redirect('goals')

    @app.route('/financial-plan/goals/<int:goal_id>/delete', methods=['POST'])
    def delete_financial_goal(goal_id):
        if is_local_test_user():
            abort(404)
        goal = FinancialGoal.query.filter_by(id=goal_id, user_id=current_user.id).first()
        if goal is None:
            abort(404)
        name = goal.name
        delete_goal_cashflow_entries(goal.transactions)
        db.session.delete(goal)
        db.session.flush()
        _normalize_financial_goal_priorities(current_user.id)
        db.session.commit()
        flash(f'Цель «{name}» удалена вместе с историей операций.', 'success')
        return _financial_plan_redirect('goals')

    @app.route('/financial-plan/goals/<int:goal_id>/move', methods=['POST'])
    def move_financial_goal(goal_id):
        if is_local_test_user():
            abort(404)
        direction = request.form.get('direction')
        if direction not in ('up', 'down'):
            abort(400)
        goals = (
            FinancialGoal.query
            .filter_by(user_id=current_user.id)
            .order_by(FinancialGoal.priority.asc(), FinancialGoal.id.asc())
            .all()
        )
        current_index = next((index for index, goal in enumerate(goals) if goal.id == goal_id), None)
        if current_index is None:
            abort(404)
        swap_index = current_index - 1 if direction == 'up' else current_index + 1
        if 0 <= swap_index < len(goals):
            goals[current_index].priority, goals[swap_index].priority = (
                goals[swap_index].priority,
                goals[current_index].priority,
            )
            db.session.flush()
            _normalize_financial_goal_priorities(current_user.id)
            db.session.commit()
        return _financial_plan_redirect('goals')

    @app.route('/financial-plan/goals/<int:goal_id>/transactions', methods=['POST'])
    def add_financial_goal_transaction(goal_id):
        if is_local_test_user():
            flash('Операции демо-целей не сохраняются.', 'info')
            return _financial_plan_redirect('goals')
        goal = FinancialGoal.query.filter_by(id=goal_id, user_id=current_user.id).first()
        if goal is None:
            abort(404)
        try:
            transaction_type = request.form.get('transaction_type', 'deposit')
            if transaction_type not in ('deposit', 'withdrawal'):
                raise ValueError('Выберите корректный тип операции.')
            amount = parse_decimal(request.form.get('amount'), 'Сумма')
            if amount <= 0:
                raise ValueError('Сумма операции должна быть больше нуля.')
            balance = get_financial_goal_balance(goal.id, current_user.id)
            if transaction_type == 'withdrawal' and amount > balance:
                raise ValueError(f'Нельзя снять больше, чем накоплено на цель «{goal.name}».')
            transaction_date = parse_date(request.form.get('transaction_date'), 'Дата операции', required=True)
            comment = str(request.form.get('comment', '')).strip() or None
            if comment and len(comment) > 255:
                raise ValueError('Комментарий не должен превышать 255 символов.')
            cashflow_ids = create_goal_cashflow_entry(
                current_user.id,
                goal.name,
                transaction_type,
                amount,
                transaction_date,
                comment,
            )
            db.session.add(FinancialGoalTransaction(
                goal_id=goal.id,
                transaction_type=transaction_type,
                amount=amount,
                transaction_date=transaction_date,
                comment=comment,
                **cashflow_ids,
            ))
            db.session.commit()
            action_label = 'Пополнение' if transaction_type == 'deposit' else 'Снятие'
            flash(f'{action_label} цели «{goal.name}» учтено.', 'success')
        except ValueError as exc:
            db.session.rollback()
            flash(str(exc), 'danger')
        except Exception:
            db.session.rollback()
            current_app.logger.exception('Failed to save financial goal transaction')
            flash('Не удалось сохранить операцию цели.', 'danger')
        return _financial_plan_redirect('goals')

    @app.route('/financial-plan/goals/<int:goal_id>/transactions/<int:transaction_id>/delete', methods=['POST'])
    def delete_financial_goal_transaction(goal_id, transaction_id):
        if is_local_test_user():
            abort(404)
        goal = FinancialGoal.query.filter_by(id=goal_id, user_id=current_user.id).first()
        if goal is None:
            abort(404)
        transaction = FinancialGoalTransaction.query.filter_by(id=transaction_id, goal_id=goal.id).first()
        if transaction is None:
            abort(404)
        balance = get_financial_goal_balance(goal.id, current_user.id)
        if transaction.transaction_type == 'deposit' and transaction.amount > balance:
            flash('Сначала удалите связанные снятия: без этого остаток станет отрицательным.', 'danger')
            return _financial_plan_redirect('goals')
        delete_goal_cashflow_entries([transaction])
        db.session.delete(transaction)
        db.session.commit()
        flash(f'Операция цели «{goal.name}» удалена.', 'success')
        return _financial_plan_redirect('goals')

    @app.route('/mortgages')
    def mortgages():
        if is_local_test_user():
            summary = get_finance_summary(None)
            active_debts = [d for d in summary['active_debts'] if d.debt_type == 'mortgage']
        else:
            active_debts = Debt.query.filter_by(status='active', user_id=current_user.id, debt_type='mortgage')
            active_debts = active_debts.order_by(db.case((Debt.next_payment_date.is_(None), 1), else_=0), Debt.next_payment_date.asc()).all()

        today = date.today()
        total_remaining = sum((Decimal(str(d.remaining_amount or 0)) for d in active_debts), Decimal('0.00'))
        total_original = sum((Decimal(str(d.total_amount or 0)) for d in active_debts), Decimal('0.00'))
        overdue_count = len([d for d in active_debts if d.next_payment_date and d.next_payment_date < today])
        nearest_debt = next((d for d in active_debts if d.next_payment_date and d.next_payment_date >= today), None)

        return render_template('mortgages.html',
            debts=active_debts,
            active_count=len(active_debts),
            total_remaining=total_remaining,
            total_original=total_original,
            overdue_count=overdue_count,
            nearest_debt=nearest_debt,
            today=today,
        )

    @app.route('/debts/<int:debt_id>/overdue')
    def debt_overdue(debt_id):
        today = date.today()
        if is_local_test_user():
            summary = get_finance_summary(None)
            debt = next((d for d in summary['active_debts'] if d.id == debt_id), None)
        else:
            debt = get_user_debt(debt_id)

        if not debt:
            return render_template('overdue_interest.html', error='Долг не найден.'), 404

        if not debt.next_payment_date or debt.next_payment_date >= today:
            return render_template('overdue_interest.html', error='Для этого долга нет просрочки.'), 400

        interest_summary = calculate_overdue_interest(debt, today=today)
        if not interest_summary:
            return render_template('overdue_interest.html', error='Для этого долга не указана процентная ставка.'), 400

        overdue_days = (today - debt.next_payment_date).days

        return render_template('overdue_interest.html',
            debt=debt,
            today=today,
            overdue_days=overdue_days,
            annual_rate=interest_summary['annual_rate'],
            daily_rate=interest_summary['daily_rate'],
            interest_per_day=interest_summary['interest_per_day'],
            total_overdue_interest=interest_summary['total_overdue_interest'],
            total_with_overdue=interest_summary['total_with_overdue'],
            interest_periods=interest_summary['periods'],
        )

    @app.route('/archive')
    def archive():
        if str(get_setting('archive_enabled', 'true')).lower() not in ('1', 'true', 'yes', 'on'):
            abort(404)
        if is_local_test_user():
            archived_debts = []
        else:
            archived_debts = Debt.query.filter_by(status='archived', user_id=current_user.id).order_by(Debt.updated_at.desc()).all()
        return render_template('archive.html', debts=archived_debts)

    @app.route('/api/init-db', methods=['POST'])
    def init_db_route():
        if not (app.debug and app.config.get('DEV_LOGIN_ENABLED', False)):
            abort(404)
        if is_local_test_user():
            return jsonify({'success': True, 'message': 'Локальный тестовый пользователь не использует базу данных.'})

        if (
            Debt.query.filter_by(user_id=current_user.id).count() == 0
            and Income.query.filter_by(user_id=current_user.id).count() == 0
            and Expense.query.filter_by(user_id=current_user.id).count() == 0
        ):
            seed_data()

        return jsonify({'success': True, 'message': 'База данных инициализирована'})


def seed_data():
    today = date.today()

    def shift_month(base_date, offset):
        month = base_date.month + offset
        year = base_date.year + (month - 1) // 12
        month = (month - 1) % 12 + 1
        return year, month

    def next_payment_day(day, threshold_day):
        if today.day < threshold_day:
            return date(today.year, today.month, day)
        year, month = shift_month(today, 1)
        return date(year, month, day)

    def previous_month_day(day, offset):
        year, month = shift_month(today, offset)
        return date(year, month, day)

    debts = [
        Debt(
            user_id=current_user.id,
            bank_name='Тинькофф',
            debt_type='credit_card',
            product_name='Тинькофф Платинум',
            total_amount=85000,
            remaining_amount=47500,
            minimum_payment=3200,
            interest_rate=28.9,
            next_payment_date=next_payment_day(25, 25),
            comment='Основная кредитная карта',
            status='active',
        ),
        Debt(
            user_id=current_user.id,
            bank_name='Сбербанк',
            debt_type='split',
            product_name='СберСплит — MacBook Pro',
            total_amount=180000,
            remaining_amount=120000,
            minimum_payment=15000,
            interest_rate=None,
            next_payment_date=next_payment_day(15, 15),
            comment='12 платежей, прошло 4',
            status='active',
        ),
        Debt(
            user_id=current_user.id,
            bank_name='Альфа-Банк',
            debt_type='credit_card',
            product_name='Альфа-Карта',
            total_amount=50000,
            remaining_amount=8200,
            minimum_payment=1500,
            interest_rate=24.5,
            next_payment_date=(today + timedelta(days=3)),
            comment='Почти погашена',
            status='active',
        ),
        Debt(
            user_id=current_user.id,
            bank_name='ВТБ',
            debt_type='split',
            product_name='ВТБ Части — iPhone 15',
            total_amount=95000,
            remaining_amount=0,
            minimum_payment=0,
            interest_rate=None,
            next_payment_date=None,
            comment='Полностью погашено',
            status='archived',
        ),
        Debt(
            user_id=current_user.id,
            bank_name='Сбербанк',
            debt_type='mortgage',
            product_name='Ипотека на квартиру',
            total_amount=3600000,
            remaining_amount=3480000,
            minimum_payment=22000,
            interest_rate=3.6,
            next_payment_date=next_payment_day(10, 10),
            comment='Ипотека на 20 лет',
            status='active',
        ),
        Debt(
            user_id=current_user.id,
            bank_name='Совкомбанк',
            debt_type='mortgage',
            product_name='Ипотека с просрочкой',
            total_amount=3600000,
            remaining_amount=3500000,
            minimum_payment=25000,
            interest_rate=14.0,
            next_payment_date=previous_month_day(15, -1),
            comment='Просроченный платёж по ипотеке 14% годовых',
            status='active',
        ),
    ]

    for debt in debts:
        db.session.add(debt)
    db.session.flush()

    payments = [
        Payment(
            debt_id=debts[0].id,
            amount=10000,
            payment_date=previous_month_day(25, -1),
            comment='Плановый платеж',
            remaining_after_payment=57500,
        ),
        Payment(
            debt_id=debts[0].id,
            amount=20000,
            payment_date=previous_month_day(25, -2),
            comment='Досрочный платеж',
            remaining_after_payment=67500,
        ),
    ]
    for p in payments:
        db.session.add(p)

    incomes = [
        Income(
            user_id=current_user.id,
            amount=85000,
            category='salary',
            source='Основная работа',
            income_date=date(today.year, today.month, 10),
            comment='Зарплата за прошлый месяц',
        ),
        Income(
            user_id=current_user.id,
            amount=15000,
            category='bonus',
            source='Премия',
            income_date=date(today.year, today.month, 5),
            comment='Премия за выполнение плана',
        ),
    ]
    for inc in incomes:
        db.session.add(inc)

    expenses = [
        Expense(
            user_id=current_user.id,
            amount=3500,
            category='products',
            title='Продукты',
            expense_date=date(today.year, today.month, 12),
            payment_method='card',
            comment='Покупка в магазине',
        ),
        Expense(
            user_id=current_user.id,
            amount=6200,
            category='transport',
            title='Транспорт',
            expense_date=date(today.year, today.month, 7),
            payment_method='card',
            comment='Проезд и такси',
        ),
    ]
    for exp in expenses:
        db.session.add(exp)

    db.session.commit()
