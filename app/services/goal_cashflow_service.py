from app.models import EmergencyFundTransaction, Expense, FinancialGoalTransaction, Income
from extensions import db


def create_goal_cashflow_entry(user_id, goal_name, transaction_type, amount, transaction_date, comment=None):
    automatic_note = f'Создано автоматически из операции цели «{goal_name}».'
    full_comment = f'{automatic_note} {comment}'.strip() if comment else automatic_note

    if transaction_type == 'deposit':
        expense = Expense(
            user_id=user_id,
            amount=amount,
            category='savings',
            title=f'Пополнение цели: {goal_name}'[:150],
            expense_date=transaction_date,
            payment_method='transfer',
            comment=full_comment,
            is_monthly=False,
        )
        db.session.add(expense)
        db.session.flush()
        return {'expense_id': expense.id, 'income_id': None}

    income = Income(
        user_id=user_id,
        amount=amount,
        category='goal_withdrawal',
        source=f'Снятие с цели: {goal_name}'[:150],
        income_date=transaction_date,
        comment=full_comment,
    )
    db.session.add(income)
    db.session.flush()
    return {'expense_id': None, 'income_id': income.id}


def delete_goal_cashflow_entries(transactions):
    expense_ids = {item.expense_id for item in transactions if item.expense_id}
    income_ids = {item.income_id for item in transactions if item.income_id}
    if expense_ids:
        Expense.query.filter(Expense.id.in_(expense_ids)).delete(synchronize_session=False)
    if income_ids:
        Income.query.filter(Income.id.in_(income_ids)).delete(synchronize_session=False)


def is_goal_expense(expense_id):
    return bool(
        EmergencyFundTransaction.query.filter_by(expense_id=expense_id).first()
        or FinancialGoalTransaction.query.filter_by(expense_id=expense_id).first()
    )


def is_goal_income(income_id):
    return bool(
        EmergencyFundTransaction.query.filter_by(income_id=income_id).first()
        or FinancialGoalTransaction.query.filter_by(income_id=income_id).first()
    )


def mark_goal_cashflow_entries(expenses=None, incomes=None):
    expenses = expenses or []
    incomes = incomes or []
    expense_ids = {item.id for item in expenses if item.id is not None}
    income_ids = {item.id for item in incomes if item.id is not None}

    linked_expense_ids = set()
    linked_income_ids = set()
    if expense_ids:
        linked_expense_ids.update(
            item[0] for item in db.session.query(EmergencyFundTransaction.expense_id)
            .filter(EmergencyFundTransaction.expense_id.in_(expense_ids)).all()
        )
        linked_expense_ids.update(
            item[0] for item in db.session.query(FinancialGoalTransaction.expense_id)
            .filter(FinancialGoalTransaction.expense_id.in_(expense_ids)).all()
        )
    if income_ids:
        linked_income_ids.update(
            item[0] for item in db.session.query(EmergencyFundTransaction.income_id)
            .filter(EmergencyFundTransaction.income_id.in_(income_ids)).all()
        )
        linked_income_ids.update(
            item[0] for item in db.session.query(FinancialGoalTransaction.income_id)
            .filter(FinancialGoalTransaction.income_id.in_(income_ids)).all()
        )

    for expense in expenses:
        expense.is_goal_transfer = expense.id in linked_expense_ids
    for income in incomes:
        income.is_goal_transfer = income.id in linked_income_ids
