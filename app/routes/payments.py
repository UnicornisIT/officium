from decimal import Decimal
from flask import current_app, jsonify, request
from app.time_utils import financial_today
from app.models import DebtPaymentPlan, Payment
from app.services.debt_service import get_user_debt
from app.services.payment_service import accept_bank_remaining, add_payment, delete_payment, update_payment
from app.utils import is_local_test_user, parse_date, parse_decimal
from extensions import db


def init_app(app):
    @app.route('/api/debts/<int:debt_id>/payments', methods=['GET'])
    def api_get_payments(debt_id):
        debt = get_user_debt(debt_id)
        if not debt:
            return jsonify({'success': False, 'error': 'Долг не найден'}), 404

        payments = Payment.query.filter_by(debt_id=debt_id).order_by(Payment.payment_date.desc()).all()
        state = debt.accrual_state()
        return jsonify({
            'success': True,
            'debt': debt.to_dict(),
            'payments': [p.to_dict(state=state) for p in payments],
            'planned_payments': [plan.to_dict() for plan in debt.payment_plans],
        })

    @app.route('/api/debts/<int:debt_id>/payment-plans', methods=['POST'])
    def api_add_payment_plan(debt_id):
        debt = None if is_local_test_user() else get_user_debt(debt_id)
        if not debt:
            return jsonify({'success': False, 'error': 'Долг не найден'}), 404
        if debt.status != 'active':
            return jsonify({'success': False, 'error': 'Нельзя планировать платеж по архивному долгу'}), 422

        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return jsonify({'success': False, 'error': 'Нет данных'}), 400
        try:
            amount = parse_decimal(data.get('amount'), 'Сумма платежа')
            due_date = parse_date(data.get('payment_date'), 'Дата по плану', required=True)
            if amount <= 0:
                raise ValueError('Сумма платежа должна быть больше нуля')
            if due_date < financial_today(debt):
                raise ValueError('Дата по плану не может быть в прошлом')
        except ValueError as exc:
            return jsonify({'success': False, 'error': str(exc)}), 422
        plan = DebtPaymentPlan(
            debt_id=debt.id, amount=amount, due_date=due_date,
            comment=str(data.get('comment') or '').strip() or None,
        )
        db.session.add(plan)
        db.session.commit()
        return jsonify({'success': True, 'plan': plan.to_dict()})

    @app.route('/api/debts/<int:debt_id>/payment-plans/<int:plan_id>', methods=['DELETE'])
    def api_delete_payment_plan(debt_id, plan_id):
        if is_local_test_user() or not get_user_debt(debt_id):
            return jsonify({'success': False, 'error': 'Долг не найден'}), 404
        if not DebtPaymentPlan.query.filter_by(id=plan_id, debt_id=debt_id).delete():
            return jsonify({'success': False, 'error': 'План платежа не найден'}), 404
        db.session.commit()
        return jsonify({'success': True})

    @app.route('/api/debts/<int:debt_id>/payments', methods=['POST'])
    def api_add_payment(debt_id):
        debt = get_user_debt(debt_id)
        if not debt:
            return jsonify({'success': False, 'error': 'Долг не найден'}), 404
        if debt.status != 'active':
            return jsonify({'success': False, 'error': 'Нельзя вносить платеж в архивный долг'}), 422

        data = request.get_json(silent=True)
        if not isinstance(data, dict) or not data:
            return jsonify({'success': False, 'error': 'Нет данных'}), 400

        try:
            amount = parse_decimal(data.get('amount'), 'Сумма платежа', required=True)
            principal_amount = parse_decimal(data.get('principal_amount'), 'Основной долг', required=False)
            interest_amount = parse_decimal(data.get('interest_amount'), 'Проценты', required=False)
            fee_amount = parse_decimal(data.get('fee_amount'), 'Комиссии', required=False)
            penalty_amount = parse_decimal(data.get('penalty_amount'), 'Неустойка', required=False)
            manual_breakdown = _manual_breakdown_flag(data)
            scheduled_payment_amount = parse_decimal(data.get('scheduled_payment_amount'), 'Обязательная часть платежа', required=False)
            bank_remaining_after_payment = parse_decimal(data.get('bank_remaining_after_payment'), 'Остаток банка после платежа', required=False)
            if amount <= 0:
                raise ValueError("Сумма платежа должна быть больше нуля")
            if scheduled_payment_amount is not None and scheduled_payment_amount < 0:
                raise ValueError('Обязательная часть платежа не может быть отрицательной')
            if scheduled_payment_amount is not None and scheduled_payment_amount > amount:
                raise ValueError('Обязательная часть не может быть больше суммы платежа')

            is_early_repayment = bool(data.get('is_early_repayment'))
            if not is_early_repayment:
                scheduled_payment_amount = None

            payment_date_str = data.get('payment_date')
            if payment_date_str:
                payment_date = parse_date(payment_date_str, 'Дата платежа')
            else:
                payment_date = financial_today(debt)

            if payment_date > financial_today(debt):
                raise ValueError('Укажите фактическую дату оплаты или запланируйте платеж')
            plan_id = data.get('plan_id')
            if plan_id is not None:
                if is_local_test_user():
                    return jsonify({'success': False, 'error': 'План платежа не найден'}), 404
                if type(plan_id) is not int or not 0 < plan_id <= 2147483647:
                    raise ValueError('Некорректный идентификатор плана платежа')
                # Consume the plan in the same transaction that records the payment.
                if not DebtPaymentPlan.query.filter_by(id=plan_id, debt_id=debt_id).delete():
                    return jsonify({'success': False, 'error': 'План платежа уже выполнен или удален'}), 404

            payment = add_payment(
                debt,
                amount,
                payment_date=payment_date,
                comment=str(data.get('comment', '')).strip() or None,
                is_early_repayment=is_early_repayment,
                scheduled_payment_amount=scheduled_payment_amount,
                principal_amount=principal_amount,
                interest_amount=interest_amount,
                fee_amount=fee_amount,
                penalty_amount=penalty_amount,
                bank_remaining_after_payment=bank_remaining_after_payment,
                manual_breakdown=manual_breakdown,
            )
            db.session.refresh(debt)
            debt_cleared = debt.accrual_state()['principal'] <= Decimal('0.00')

            return jsonify({
                'success': True,
                'payment': payment.to_dict(),
                'debt': debt.to_dict(),
                'debt_cleared': debt_cleared,
                'next_payment_date_advanced': bool(getattr(payment, 'next_payment_date_advanced', False)),
            })

        except ValueError as e:
            db.session.rollback()
            return jsonify({'success': False, 'error': str(e)}), 422
        except Exception:
            db.session.rollback()
            current_app.logger.exception('Failed to add a debt payment')
            return jsonify({'success': False, 'error': 'Не удалось сохранить платеж'}), 500

    @app.route('/api/debts/<int:debt_id>/payments/<int:payment_id>', methods=['PUT'])
    def api_update_payment(debt_id, payment_id):
        debt = get_user_debt(debt_id)
        if not debt:
            return jsonify({'success': False, 'error': 'Долг не найден'}), 404

        payment = Payment.query.filter_by(id=payment_id, debt_id=debt_id).first()
        if not payment:
            return jsonify({'success': False, 'error': 'Платеж не найден'}), 404

        data = request.get_json(silent=True)
        if not data:
            return jsonify({'success': False, 'error': 'Нет данных'}), 400

        try:
            amount = parse_decimal(data.get('amount'), 'Сумма платежа', required=True)
            principal_amount = parse_decimal(data.get('principal_amount'), 'Основной долг', required=False)
            interest_amount = parse_decimal(data.get('interest_amount'), 'Проценты', required=False)
            fee_amount = parse_decimal(data.get('fee_amount'), 'Комиссии', required=False)
            penalty_amount = parse_decimal(data.get('penalty_amount'), 'Неустойка', required=False)
            manual_breakdown = _manual_breakdown_flag(data)
            scheduled_payment_amount = parse_decimal(
                data.get('scheduled_payment_amount', payment.scheduled_payment_amount),
                'Обязательная часть платежа',
                required=False,
            )
            bank_remaining_after_payment = parse_decimal(data.get('bank_remaining_after_payment'), 'Остаток банка после платежа', required=False)
            if amount <= 0:
                raise ValueError('Сумма платежа должна быть больше нуля')
            if scheduled_payment_amount is not None and scheduled_payment_amount < 0:
                raise ValueError('Обязательная часть платежа не может быть отрицательной')
            if scheduled_payment_amount is not None and scheduled_payment_amount > amount:
                raise ValueError('Обязательная часть не может быть больше суммы платежа')

            is_early_repayment = bool(data.get('is_early_repayment'))
            if not is_early_repayment:
                scheduled_payment_amount = None

            payment_date = parse_date(data.get('payment_date'), 'Дата платежа', required=True)
            if payment_date > financial_today(debt):
                raise ValueError('Укажите фактическую дату оплаты')
            payment = update_payment(
                debt,
                payment,
                amount=amount,
                payment_date=payment_date,
                comment=str(data.get('comment', '')).strip() or None,
                is_early_repayment=is_early_repayment,
                scheduled_payment_amount=scheduled_payment_amount,
                principal_amount=principal_amount,
                interest_amount=interest_amount,
                fee_amount=fee_amount,
                penalty_amount=penalty_amount,
                bank_remaining_after_payment=bank_remaining_after_payment,
                manual_breakdown=manual_breakdown,
            )

            db.session.refresh(debt)
            return jsonify({
                'success': True,
                'payment': payment.to_dict(),
                'debt': debt.to_dict(),
            })

        except ValueError as e:
            db.session.rollback()
            return jsonify({'success': False, 'error': str(e)}), 422
        except Exception:
            db.session.rollback()
            current_app.logger.exception('Failed to update a debt payment')
            return jsonify({'success': False, 'error': 'Не удалось обновить платеж'}), 500

    @app.route('/api/debts/<int:debt_id>/payments/<int:payment_id>', methods=['DELETE'])
    def api_delete_payment(debt_id, payment_id):
        debt = get_user_debt(debt_id)
        if not debt:
            return jsonify({'success': False, 'error': 'Долг не найден'}), 404

        payment = Payment.query.filter_by(id=payment_id, debt_id=debt_id).first()
        if not payment:
            return jsonify({'success': False, 'error': 'Платеж не найден'}), 404

        try:
            delete_payment(debt, payment)
            db.session.refresh(debt)
            return jsonify({'success': True, 'debt': debt.to_dict()})
        except ValueError as e:
            db.session.rollback()
            return jsonify({'success': False, 'error': str(e)}), 422
        except Exception:
            db.session.rollback()
            current_app.logger.exception('Failed to delete a debt payment')
            return jsonify({'success': False, 'error': 'Не удалось удалить платеж'}), 500

    @app.route('/api/debts/<int:debt_id>/payments/<int:payment_id>/accept-bank-remaining', methods=['POST'])
    def api_accept_bank_remaining(debt_id, payment_id):
        debt = get_user_debt(debt_id)
        if not debt:
            return jsonify({'success': False, 'error': 'Долг не найден'}), 404

        payment = Payment.query.filter_by(id=payment_id, debt_id=debt_id).first()
        if not payment:
            return jsonify({'success': False, 'error': 'Платеж не найден'}), 404

        try:
            payment = accept_bank_remaining(debt, payment)
            db.session.refresh(debt)
            return jsonify({'success': True, 'payment': payment.to_dict(), 'debt': debt.to_dict()})
        except ValueError as e:
            db.session.rollback()
            return jsonify({'success': False, 'error': str(e)}), 422
        except Exception:
            db.session.rollback()
            current_app.logger.exception('Failed to accept the bank balance for a payment')
            return jsonify({'success': False, 'error': 'Не удалось принять остаток банка'}), 500


def _manual_breakdown_flag(data):
    """None when the client did not say; then any typed amount means manual."""
    if 'manual_breakdown' not in data or data.get('manual_breakdown') is None:
        return None
    return bool(data.get('manual_breakdown'))
