from datetime import date, datetime, timedelta
from io import StringIO
import csv
from flask import abort, jsonify, redirect, render_template, request, url_for
from flask_login import current_user
from extensions import db
from app.models import Debt, Income, Expense, Payment
from app.services.finance_summary_service import get_finance_summary
from app.services.debt_interest_service import calculate_overdue_interest
from app.services.debt_service import get_user_debt


def is_local_test_user():
    return getattr(current_user, 'is_local_test_user', False)


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

    @app.route('/mortgages')
    def mortgages():
        if is_local_test_user():
            summary = get_finance_summary(None)
            active_debts = [d for d in summary['active_debts'] if d.debt_type == 'mortgage']
        else:
            active_debts = Debt.query.filter_by(status='active', user_id=current_user.id, debt_type='mortgage')
            active_debts = active_debts.order_by(db.case((Debt.next_payment_date.is_(None), 1), else_=0), Debt.next_payment_date.asc()).all()

        today = date.today()
        total_remaining = sum(float(d.remaining_amount) for d in active_debts)
        total_original = sum(float(d.total_amount) for d in active_debts)
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
        if is_local_test_user():
            archived_debts = []
        else:
            archived_debts = Debt.query.filter_by(status='archived', user_id=current_user.id).order_by(Debt.updated_at.desc()).all()
        return render_template('archive.html', debts=archived_debts)

    @app.route('/api/init-db', methods=['POST'])
    def init_db_route():
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
