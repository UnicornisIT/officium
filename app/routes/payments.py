from datetime import date
from flask import jsonify, request
from app.models import Payment
from app.services.debt_service import get_user_debt
from app.services.payment_service import add_payment, update_payment
from app.utils import parse_date, parse_decimal
from extensions import db


def init_app(app):
    @app.route('/api/debts/<int:debt_id>/payments', methods=['GET'])
    def api_get_payments(debt_id):
        debt = get_user_debt(debt_id)
        if not debt:
            return jsonify({'success': False, 'error': 'Долг не найден'}), 404

        payments = Payment.query.filter_by(debt_id=debt_id).order_by(Payment.payment_date.desc()).all()
        return jsonify({
            'success': True,
            'debt': debt.to_dict(),
            'payments': [p.to_dict() for p in payments]
        })

    @app.route('/api/debts/<int:debt_id>/payments', methods=['POST'])
    def api_add_payment(debt_id):
        debt = get_user_debt(debt_id)
        if not debt:
            return jsonify({'success': False, 'error': 'Долг не найден'}), 404
        if debt.status != 'active':
            return jsonify({'success': False, 'error': 'Нельзя вносить платеж в архивный долг'}), 422

        data = request.get_json()
        if not data:
            return jsonify({'success': False, 'error': 'Нет данных'}), 400

        try:
            amount = parse_decimal(data.get('amount'), 'Сумма платежа', required=True)
            principal_amount = parse_decimal(data.get('principal_amount'), 'Основной долг', required=False)
            interest_amount = parse_decimal(data.get('interest_amount'), 'Проценты', required=False)
            fee_amount = parse_decimal(data.get('fee_amount'), 'Комиссии', required=False)
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
                payment_date = date.today()

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
                bank_remaining_after_payment=bank_remaining_after_payment,
            )
            debt_cleared = float(debt.remaining_amount) <= 0.01

            return jsonify({
                'success': True,
                'payment': payment.to_dict(),
                'debt': debt.to_dict(),
                'debt_cleared': debt_cleared,
                'next_payment_date_advanced': bool(getattr(payment, 'next_payment_date_advanced', False)),
            })

        except ValueError as e:
            return jsonify({'success': False, 'error': str(e)}), 422
        except Exception as e:
            db.session.rollback()
            return jsonify({'success': False, 'error': f'Ошибка сервера: {str(e)}'}), 500

    @app.route('/api/debts/<int:debt_id>/payments/<int:payment_id>', methods=['PUT'])
    def api_update_payment(debt_id, payment_id):
        debt = get_user_debt(debt_id)
        if not debt:
            return jsonify({'success': False, 'error': 'Долг не найден'}), 404

        payment = Payment.query.filter_by(id=payment_id, debt_id=debt_id).first()
        if not payment:
            return jsonify({'success': False, 'error': 'Платеж не найден'}), 404

        data = request.get_json()
        if not data:
            return jsonify({'success': False, 'error': 'Нет данных'}), 400

        try:
            amount = parse_decimal(data.get('amount'), 'Сумма платежа', required=True)
            principal_amount = parse_decimal(data.get('principal_amount'), 'Основной долг', required=False)
            interest_amount = parse_decimal(data.get('interest_amount'), 'Проценты', required=False)
            fee_amount = parse_decimal(data.get('fee_amount'), 'Комиссии', required=False)
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
                bank_remaining_after_payment=bank_remaining_after_payment,
            )

            return jsonify({
                'success': True,
                'payment': payment.to_dict(),
                'debt': debt.to_dict(),
            })

        except ValueError as e:
            db.session.rollback()
            return jsonify({'success': False, 'error': str(e)}), 422
        except Exception as e:
            db.session.rollback()
            return jsonify({'success': False, 'error': f'Ошибка сервера: {str(e)}'}), 500
