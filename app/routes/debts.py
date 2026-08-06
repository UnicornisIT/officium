from datetime import datetime
from flask import jsonify, request
from flask_login import current_user
from app.models import Debt, SplitPurchase
from app.services.debt_service import get_user_debt
from app.services.debt_schedule_service import build_debt_payment_schedule
from app.utils import is_local_test_user, parse_date, parse_decimal
from extensions import db


DEBT_TYPES = ('credit_card', 'consumer_credit', 'split', 'mortgage')
REPAYMENT_TYPES = ('annuity', 'differentiated')
DAY_COUNT_CONVENTIONS = ('actual_year', 'fixed_365', 'fixed_366')
EARLY_REPAYMENT_STRATEGIES = ('reduce_term', 'reduce_payment')


def init_app(app):
    @app.route('/api/debts', methods=['GET'])
    def api_get_debts():
        if is_local_test_user():
            debts = get_demo_debts()
            status = request.args.get('status', 'active')
            type_filter = request.args.get('type', '').strip()
            filtered = [d for d in debts if d.status == status]
            if type_filter:
                filtered = [d for d in filtered if d.debt_type == type_filter]
            return jsonify({'success': True, 'debts': [d.to_dict() for d in filtered]})

        status = request.args.get('status', 'active')
        bank_filter = request.args.get('bank', '').strip()
        type_filter = request.args.get('type', '').strip()

        query = Debt.query.filter_by(status=status, user_id=current_user.id)
        if bank_filter:
            query = query.filter(Debt.bank_name.ilike(f'%{bank_filter}%'))
        if type_filter:
            query = query.filter_by(debt_type=type_filter)

        debts = query.order_by(db.case((Debt.next_payment_date.is_(None), 1), else_=0), Debt.next_payment_date.asc()).all()
        return jsonify({'success': True, 'debts': [d.to_dict() for d in debts]})

    @app.route('/api/debts', methods=['POST'])
    def api_create_debt():
        data = request.get_json()
        if not data:
            return jsonify({'success': False, 'error': 'Нет данных'}), 400

        try:
            bank_name = str(data.get('bank_name', '')).strip()
            if not bank_name:
                raise ValueError("Название банка обязательно")

            debt_type = str(data.get('debt_type', '')).strip()
            if debt_type not in DEBT_TYPES:
                raise ValueError('Тип долга: выберите корректный вариант')

            product_name = str(data.get('product_name', '')).strip()
            if not product_name:
                raise ValueError("Название продукта/карты обязательно")

            total_amount = parse_decimal(data.get('total_amount'), 'Сумма долга', required=True)
            remaining_amount = parse_decimal(data.get('remaining_amount'), 'Остаток долга', required=True)
            minimum_payment = parse_decimal(data.get('minimum_payment'), 'Минимальный платеж', required=False)
            interest_rate = parse_decimal(data.get('interest_rate'), 'Процентная ставка', required=False)
            interest_rate_after_change = parse_decimal(data.get('interest_rate_after_change'), 'Новая процентная ставка', required=False)
            interest_rate_change_date = parse_date(data.get('interest_rate_change_date'), 'Дата смены ставки')
            _validate_interest_rate_change(interest_rate_after_change, interest_rate_change_date)
            next_payment_date = parse_date(data.get('next_payment_date'), 'Дата следующего платежа')
            is_payment_recurring = bool(data.get('is_payment_recurring')) and next_payment_date is not None
            repayment_type = _choice(data.get('repayment_type'), REPAYMENT_TYPES, 'annuity', 'Тип графика')
            day_count_convention = _choice(data.get('day_count_convention'), DAY_COUNT_CONVENTIONS, 'actual_year', 'База дней')
            include_payment_day = bool(data.get('include_payment_day'))
            interest_period_start_date = parse_date(data.get('interest_period_start_date'), 'Начало процентного периода')
            early_repayment_strategy = _choice(data.get('early_repayment_strategy'), EARLY_REPAYMENT_STRATEGIES, 'reduce_term', 'Досрочное погашение')
            loan_term_months = _parse_positive_int(data.get('loan_term_months'), 'Срок в месяцах')
            monthly_fee_amount = parse_decimal(data.get('monthly_fee_amount'), 'Ежемесячные комиссии', required=False) or 0
            bank_remaining_amount = parse_decimal(data.get('bank_remaining_amount'), 'Остаток по банку', required=False)

            if remaining_amount > total_amount:
                raise ValueError("Остаток долга не может превышать общую сумму")

            debt = Debt(
                user_id=current_user.id,
                bank_name=bank_name,
                debt_type=debt_type,
                product_name=product_name,
                total_amount=total_amount,
                remaining_amount=remaining_amount,
                minimum_payment=minimum_payment,
                interest_rate=interest_rate,
                interest_rate_after_change=interest_rate_after_change,
                interest_rate_change_date=interest_rate_change_date,
                next_payment_date=next_payment_date,
                is_payment_recurring=is_payment_recurring,
                repayment_type=repayment_type,
                day_count_convention=day_count_convention,
                include_payment_day=include_payment_day,
                interest_period_start_date=interest_period_start_date,
                early_repayment_strategy=early_repayment_strategy,
                loan_term_months=loan_term_months,
                monthly_fee_amount=monthly_fee_amount,
                bank_remaining_amount=bank_remaining_amount,
                comment=str(data.get('comment', '')).strip() or None,
                status='active',
            )
            db.session.add(debt)
            db.session.commit()
            return jsonify({'success': True, 'debt': debt.to_dict()}), 201

        except ValueError as e:
            return jsonify({'success': False, 'error': str(e)}), 422
        except Exception as e:
            db.session.rollback()
            return jsonify({'success': False, 'error': f'Ошибка сервера: {str(e)}'}), 500

    @app.route('/api/debts/<int:debt_id>', methods=['GET'])
    def api_get_debt(debt_id):
        debt = get_user_debt(debt_id)
        if not debt:
            return jsonify({'success': False, 'error': 'Долг не найден'}), 404
        return jsonify({'success': True, 'debt': debt.to_dict()})

    @app.route('/api/debts/<int:debt_id>/schedule', methods=['GET'])
    def api_get_debt_schedule(debt_id):
        debt = get_user_debt(debt_id)
        if not debt:
            return jsonify({'success': False, 'error': 'Долг не найден'}), 404
        try:
            schedule = build_debt_payment_schedule(debt)
            return jsonify({'success': True, 'schedule': schedule})
        except ValueError as e:
            return jsonify({'success': False, 'error': str(e)}), 422

    @app.route('/api/debts/<int:debt_id>/split-purchases', methods=['POST'])
    def api_add_split_purchase(debt_id):
        debt = get_user_debt(debt_id)
        if not debt:
            return jsonify({'success': False, 'error': 'Долг не найден'}), 404
        if debt.status != 'active':
            return jsonify({'success': False, 'error': 'Нельзя добавлять покупку в архивный долг'}), 422
        if debt.debt_type != 'split':
            return jsonify({'success': False, 'error': 'Покупки можно добавлять только в Сплит / Рассрочку'}), 422

        data = request.get_json()
        if not data:
            return jsonify({'success': False, 'error': 'Нет данных'}), 400

        try:
            amount = parse_decimal(data.get('amount'), 'Сумма покупки', required=True)
            if amount <= 0:
                raise ValueError('Сумма покупки должна быть больше нуля')

            purchase_date = parse_date(data.get('purchase_date'), 'Дата покупки', required=True)
            installments_count = _parse_positive_int(data.get('installments_count') or 4, 'Количество платежей') or 4
            if installments_count > 24:
                raise ValueError('Количество платежей не должно быть больше 24')

            purchase = SplitPurchase(
                debt_id=debt.id,
                title=str(data.get('title', '')).strip() or None,
                amount=amount,
                purchase_date=purchase_date,
                installments_count=installments_count,
            )
            debt.total_amount = (debt.total_amount or 0) + amount
            debt.remaining_amount = (debt.remaining_amount or 0) + amount
            debt.updated_at = datetime.utcnow()

            if is_local_test_user():
                purchase.id = max((item.id or 0 for item in getattr(debt, 'split_purchases', []) or []), default=0) + 1
                debt.split_purchases.append(purchase)
            else:
                db.session.add(purchase)
                db.session.commit()

            schedule = build_debt_payment_schedule(debt)
            return jsonify({
                'success': True,
                'purchase': purchase.to_dict(),
                'debt': debt.to_dict(),
                'schedule': schedule,
            }), 201

        except ValueError as e:
            db.session.rollback()
            return jsonify({'success': False, 'error': str(e)}), 422
        except Exception as e:
            db.session.rollback()
            return jsonify({'success': False, 'error': f'Ошибка сервера: {str(e)}'}), 500

    @app.route('/api/debts/<int:debt_id>', methods=['PUT'])
    def api_update_debt(debt_id):
        debt = get_user_debt(debt_id)
        if not debt:
            return jsonify({'success': False, 'error': 'Долг не найден'}), 404

        data = request.get_json()
        if not data:
            return jsonify({'success': False, 'error': 'Нет данных'}), 400

        try:
            if 'bank_name' in data:
                bank_name = str(data['bank_name']).strip()
                if not bank_name:
                    raise ValueError("Название банка обязательно")
                debt.bank_name = bank_name

            if 'debt_type' in data:
                if data['debt_type'] not in DEBT_TYPES:
                    raise ValueError("Некорректный тип долга")
                debt.debt_type = data['debt_type']

            if 'product_name' in data:
                product_name = str(data['product_name']).strip()
                if not product_name:
                    raise ValueError("Название продукта обязательно")
                debt.product_name = product_name

            if 'total_amount' in data:
                debt.total_amount = parse_decimal(data['total_amount'], 'Сумма долга', required=True)
            if 'remaining_amount' in data:
                debt.remaining_amount = parse_decimal(data['remaining_amount'], 'Остаток долга', required=True)
            if 'minimum_payment' in data:
                debt.minimum_payment = parse_decimal(data['minimum_payment'], 'Минимальный платеж', required=False)
            if 'interest_rate' in data:
                debt.interest_rate = parse_decimal(data['interest_rate'], 'Процентная ставка', required=False)
            if 'interest_rate_after_change' in data or 'interest_rate_change_date' in data:
                interest_rate_after_change = parse_decimal(data.get('interest_rate_after_change'), 'Новая процентная ставка', required=False)
                interest_rate_change_date = parse_date(data.get('interest_rate_change_date'), 'Дата смены ставки')
                _validate_interest_rate_change(interest_rate_after_change, interest_rate_change_date)
                debt.interest_rate_after_change = interest_rate_after_change
                debt.interest_rate_change_date = interest_rate_change_date
            if 'next_payment_date' in data:
                debt.next_payment_date = parse_date(data['next_payment_date'], 'Дата следующего платежа')
            if 'is_payment_recurring' in data:
                debt.is_payment_recurring = bool(data['is_payment_recurring']) and debt.next_payment_date is not None
            if 'repayment_type' in data:
                debt.repayment_type = _choice(data.get('repayment_type'), REPAYMENT_TYPES, 'annuity', 'Тип графика')
            if 'day_count_convention' in data:
                debt.day_count_convention = _choice(data.get('day_count_convention'), DAY_COUNT_CONVENTIONS, 'actual_year', 'База дней')
            if 'include_payment_day' in data:
                debt.include_payment_day = bool(data['include_payment_day'])
            if 'interest_period_start_date' in data:
                debt.interest_period_start_date = parse_date(data.get('interest_period_start_date'), 'Начало процентного периода')
            if 'early_repayment_strategy' in data:
                debt.early_repayment_strategy = _choice(data.get('early_repayment_strategy'), EARLY_REPAYMENT_STRATEGIES, 'reduce_term', 'Досрочное погашение')
            if 'loan_term_months' in data:
                debt.loan_term_months = _parse_positive_int(data.get('loan_term_months'), 'Срок в месяцах')
            if 'monthly_fee_amount' in data:
                debt.monthly_fee_amount = parse_decimal(data.get('monthly_fee_amount'), 'Ежемесячные комиссии', required=False) or 0
            if 'bank_remaining_amount' in data:
                debt.bank_remaining_amount = parse_decimal(data.get('bank_remaining_amount'), 'Остаток по банку', required=False)
            if 'comment' in data:
                debt.comment = str(data['comment']).strip() or None

            if float(debt.remaining_amount) > float(debt.total_amount):
                raise ValueError("Остаток долга не может превышать общую сумму")

            debt.updated_at = datetime.utcnow()
            if not is_local_test_user():
                db.session.commit()
            return jsonify({'success': True, 'debt': debt.to_dict()})

        except ValueError as e:
            return jsonify({'success': False, 'error': str(e)}), 422
        except Exception as e:
            db.session.rollback()
            return jsonify({'success': False, 'error': f'Ошибка сервера: {str(e)}'}), 500

    @app.route('/api/debts/<int:debt_id>/archive', methods=['POST'])
    def api_archive_debt(debt_id):
        debt = get_user_debt(debt_id)
        if not debt:
            return jsonify({'success': False, 'error': 'Долг не найден'}), 404

        debt.status = 'archived'
        debt.updated_at = datetime.utcnow()
        if not is_local_test_user():
            db.session.commit()
        return jsonify({'success': True, 'message': 'Карточка перемещена в архив'})

    @app.route('/api/debts/<int:debt_id>/restore', methods=['POST'])
    def api_restore_debt(debt_id):
        debt = get_user_debt(debt_id)
        if not debt:
            return jsonify({'success': False, 'error': 'Долг не найден'}), 404

        debt.status = 'active'
        debt.updated_at = datetime.utcnow()
        if not is_local_test_user():
            db.session.commit()
        return jsonify({'success': True, 'message': 'Карточка восстановлена', 'debt': debt.to_dict()})

    @app.route('/api/debts/<int:debt_id>/delete', methods=['DELETE'])
    def api_delete_debt(debt_id):
        debt = get_user_debt(debt_id)
        if not debt:
            return jsonify({'success': False, 'error': 'Долг не найден'}), 404

        if not is_local_test_user():
            db.session.delete(debt)
            db.session.commit()
        else:
            from app.services.debt_service import delete_demo_debt
            delete_demo_debt(debt_id)
        return jsonify({'success': True, 'message': 'Карточка удалена'})


def _validate_interest_rate_change(interest_rate_after_change, interest_rate_change_date):
    if interest_rate_after_change is None and interest_rate_change_date is None:
        return
    if interest_rate_after_change is None:
        raise ValueError('Укажите новую процентную ставку или очистите дату смены ставки')
    if interest_rate_change_date is None:
        raise ValueError('Укажите дату смены ставки или очистите новую процентную ставку')


def _choice(value, allowed, default, field_name):
    text = str(value or '').strip()
    if not text:
        return default
    if text not in allowed:
        raise ValueError(f'{field_name}: выберите корректный вариант')
    return text


def _parse_positive_int(value, field_name):
    if value is None or str(value).strip() == '':
        return None
    try:
        parsed = int(str(value).strip())
    except ValueError:
        raise ValueError(f'{field_name}: укажите целое число')
    if parsed <= 0:
        raise ValueError(f'{field_name}: значение должно быть больше нуля')
    return parsed
