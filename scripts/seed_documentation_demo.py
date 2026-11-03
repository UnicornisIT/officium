"""Fictional documentation data; ONLY the disposable Playwright database."""
import argparse
import json
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import create_app
from app.models import (User, AppSetting, Income, Expense, Debt, FinancialGoal,
                        FinancialGoalTransaction, EmergencyFundTransaction,
                        VehicleOdometerReading, VehicleDocument, VehicleMaintenanceItem,
                        VehicleDefect, RegistrationCountry)
from app.services import vehicle_service as vehicles, vehicle_cost_service as costs
from extensions import db


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('database', type=Path)
    args = parser.parse_args()
    database = args.database.resolve()
    if database.name != 'officium-playwright.sqlite' or not database.is_file():
        parser.error('Expected the existing disposable officium-playwright.sqlite database')
    app = create_app(dict(TESTING=True, SECRET_KEY='documentation-fixtures-only',
        SQLALCHEMY_DATABASE_URI='sqlite:///' + database.as_posix(), SQLALCHEMY_ENGINE_OPTIONS={},
        VEHICLES_ENABLED=True, APP_TIMEZONE='Europe/Moscow'))
    with app.app_context():
        user = User.query.filter_by(telegram_id=-1000000001).one()
        user.telegram_id = 8100000000 + user.id
        user.username = 'officium_demo'
        user.first_name = 'Анна'
        user.last_name = 'Демонстрация'
        user.notification_timezone = 'Europe/Moscow'
        user.notification_delivery_time = '09:30'
        user.hide_amounts_on_open = False
        db.session.add(AppSetting(key='vehicle_notifications_enabled', value='true'))
        day = date.today()
        first = day.replace(day=1)
        db.session.add_all([
            Income(user_id=user.id, amount=125000, category='salary', source='Учебная компания', income_date=first),
            Expense(user_id=user.id, amount=24000, category='rent', title='Аренда квартиры', expense_date=first, is_monthly=True, monthly_group_id='00000000-0000-0000-0000-000000000001', monthly_anchor_day=1),
            Expense(user_id=user.id, amount=4850, category='products', title='Продукты на неделю', expense_date=day, payment_method='card'),
            Expense(user_id=user.id, amount=1500, category='communication', title='Связь и интернет', expense_date=first, payment_method='card'),
            Debt(user_id=user.id, bank_name='Учебный банк', product_name='Потребительский кредит', debt_type='consumer_credit', total_amount=300000, remaining_amount=180000, minimum_payment=12500, interest_rate=18, next_payment_date=day+timedelta(days=7), loan_term_months=36, status='active'),
            Debt(user_id=user.id, bank_name='Демонстрационный банк', product_name='Кредитная карта', debt_type='credit_card', total_amount=150000, remaining_amount=42000, minimum_payment=4200, interest_rate=26, next_payment_date=day+timedelta(days=12), status='active'),
            EmergencyFundTransaction(user_id=user.id, transaction_type='deposit', amount=60000, transaction_date=first),
        ])
        goal = FinancialGoal(user_id=user.id, name='Путешествие', target_amount=200000,
            monthly_contribution=15000, target_date=day+timedelta(days=180), priority=1)
        db.session.add(goal)
        db.session.flush()
        db.session.add(FinancialGoalTransaction(goal_id=goal.id, transaction_type='deposit',
            amount=35000, transaction_date=first))
        country = RegistrationCountry.query.filter_by(code='RU').one()
        car = vehicles.create_vehicle(user, dict(name='Семейный автомобиль', make='Toyota',
            model='Mark X', year='2010', vin='GRX130-0000001', plate='А123ВС777',
            registration_country_id=country.id, color='#5276ba'), commit=False)
        vehicles.create_vehicle(user, dict(name='Городской автомобиль', make='LADA',
            model='Granta', year='2022', color='#bfc5ce'), commit=False)
        db.session.add_all([
            VehicleOdometerReading(vehicle_id=car.id, reading_date=day, odometer_km=87450, source='manual'),
            VehicleDocument(vehicle_id=car.id, kind='osago', title='Учебный полис', issued_date=day-timedelta(days=353), expires_at=day+timedelta(days=12), amount=7800),
            VehicleMaintenanceItem(vehicle_id=car.id, name='Замена масла', interval_km=10000, last_done_km=78000, last_done_date=day-timedelta(days=170), is_active=True),
            VehicleDefect(vehicle_id=car.id, title='Проверить давление в шинах', severity='minor', status='open', found_date=day),
        ])
        db.session.commit()
        costs.create_vehicle_spend(user, car, amount='4500', cost_date=day.isoformat(),
            kind='fuel', title='Заправка на учебной АЗС', payment_method='card')
        costs.create_vehicle_spend(user, car, amount='3200', cost_date=day.isoformat(),
            kind='service', title='Плановое обслуживание', payment_method='card')
        print(json.dumps(dict(vehicle_id=car.id, user_id=user.id)))


if __name__ == '__main__':
    main()
