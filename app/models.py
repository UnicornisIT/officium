from calendar import monthrange
from datetime import datetime, date

from dateutil.relativedelta import relativedelta
from flask_login import UserMixin
from extensions import db


class User(UserMixin, db.Model):
    __tablename__ = 'users'

    id = db.Column(db.Integer, primary_key=True)
    telegram_id = db.Column(db.BigInteger, unique=True, nullable=False)
    username = db.Column(db.String(80), nullable=True)
    first_name = db.Column(db.String(100), nullable=True)
    last_name = db.Column(db.String(100), nullable=True)
    photo_url = db.Column(db.String(255), nullable=True)
    auth_date = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    role = db.Column(db.Enum('user', 'admin', 'superadmin'), nullable=False, default='user')
    is_blocked = db.Column(db.Boolean, default=False, nullable=False)
    last_login_ip = db.Column(db.String(100), nullable=True)
    last_user_agent = db.Column(db.Text, nullable=True)
    login_count = db.Column(db.Integer, default=0, nullable=False)
    google_id = db.Column(db.String(50), unique=True, nullable=True)
    email = db.Column(db.String(120), nullable=True, index=True)
    avatar_url = db.Column(db.String(255), nullable=True)

    debts = db.relationship('Debt', back_populates='user', lazy=True, cascade='all, delete-orphan')
    incomes = db.relationship('Income', back_populates='user', lazy=True, cascade='all, delete-orphan')
    expenses = db.relationship('Expense', back_populates='user', lazy=True, cascade='all, delete-orphan')
    financial_plan_preference = db.relationship(
        'FinancialPlanPreference',
        back_populates='user',
        uselist=False,
        cascade='all, delete-orphan',
    )
    emergency_fund_transactions = db.relationship(
        'EmergencyFundTransaction',
        back_populates='user',
        lazy=True,
        cascade='all, delete-orphan',
    )
    financial_goals = db.relationship(
        'FinancialGoal',
        back_populates='user',
        lazy=True,
        cascade='all, delete-orphan',
        order_by='FinancialGoal.priority',
    )
    activity_logs = db.relationship('ActivityLog', back_populates='user', lazy=True, cascade='all, delete-orphan')

    @property
    def is_admin(self):
        return self.role in ('admin', 'superadmin')

    @property
    def is_superadmin(self):
        return self.role == 'superadmin'

    def __repr__(self):
        return f'<User {self.telegram_id}>'


class Debt(db.Model):
    __tablename__ = 'debts'
    __table_args__ = (
        db.Index('ix_debts_user_status_due', 'user_id', 'status', 'next_payment_date'),
    )
    DEBT_TYPE_LABELS = {
        'credit_card': 'Кредитная карта',
        'consumer_credit': 'Потребительский кредит',
        'mortgage': 'Ипотека',
        'split': 'Сплит',
    }

    id = db.Column(db.Integer, primary_key=True)
    bank_name = db.Column(db.String(100), nullable=False)
    debt_type = db.Column(
        db.Enum('credit_card', 'consumer_credit', 'split', 'mortgage'),
        nullable=False,
    )
    product_name = db.Column(db.String(150), nullable=False)
    total_amount = db.Column(db.Numeric(12, 2), nullable=False)
    remaining_amount = db.Column(db.Numeric(12, 2), nullable=False)
    minimum_payment = db.Column(db.Numeric(12, 2), nullable=True)
    first_payment_amount = db.Column(db.Numeric(12, 2), nullable=True)
    interest_rate = db.Column(db.Numeric(5, 2), nullable=True)
    interest_rate_after_change = db.Column(db.Numeric(5, 2), nullable=True)
    interest_rate_change_date = db.Column(db.Date, nullable=True)
    next_payment_date = db.Column(db.Date, nullable=True)
    is_payment_recurring = db.Column(db.Boolean, default=False, nullable=False)
    repayment_type = db.Column(db.Enum('annuity', 'differentiated'), default='annuity', nullable=False)
    day_count_convention = db.Column(db.Enum('actual_year', 'fixed_365', 'fixed_366'), default='actual_year', nullable=False)
    include_payment_day = db.Column(db.Boolean, default=False, nullable=False)
    interest_period_start_date = db.Column(db.Date, nullable=True)
    early_repayment_strategy = db.Column(db.Enum('reduce_term', 'reduce_payment'), default='reduce_term', nullable=False)
    early_repayment_enabled = db.Column(db.Boolean, default=False, nullable=False)
    planned_early_repayment_amount = db.Column(db.Numeric(12, 2), nullable=True)
    loan_term_months = db.Column(db.Integer, nullable=True)
    monthly_fee_amount = db.Column(db.Numeric(12, 2), default=0, nullable=False)
    bank_remaining_amount = db.Column(db.Numeric(12, 2), nullable=True)
    comment = db.Column(db.Text, nullable=True)
    status = db.Column(db.Enum('active', 'archived'), nullable=False, default='active')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    user = db.relationship('User', back_populates='debts')
    payments = db.relationship('Payment', backref='debt', lazy=True, cascade='all, delete-orphan')
    split_purchases = db.relationship('SplitPurchase', back_populates='debt', lazy=True, cascade='all, delete-orphan')

    def interest_rate_for(self, value_date=None):
        check_date = value_date or date.today()
        if (
            self.interest_rate_after_change is not None
            and self.interest_rate_change_date is not None
            and check_date >= self.interest_rate_change_date
        ):
            return self.interest_rate_after_change
        return self.interest_rate

    def effective_next_payment_date(self, today=None):
        today = today or date.today()
        if self.debt_type != 'split' or not self.next_payment_date or float(self.remaining_amount or 0) <= 0:
            return self.next_payment_date

        paid_payments = [
            payment for payment in self.payments
            if (
                payment.payment_date
                and payment.payment_date <= today
                and not getattr(payment, 'is_early_repayment', False)
            )
        ]
        if not paid_payments:
            return self.next_payment_date

        last_payment_date = max(payment.payment_date for payment in paid_payments)
        if self.next_payment_date > last_payment_date:
            return self.next_payment_date

        anchor_day = last_payment_date.day
        second_day = anchor_day + 15 if anchor_day <= 15 else anchor_day - 15
        payment_days = sorted({max(min(anchor_day, 31), 1), max(min(second_day, 31), 1)})
        for month_offset in range(0, 24):
            month_start = date(last_payment_date.year, last_payment_date.month, 1) + relativedelta(months=month_offset)
            for day in payment_days:
                candidate = date(month_start.year, month_start.month, min(day, monthrange(month_start.year, month_start.month)[1]))
                if candidate > last_payment_date:
                    return candidate

        return self.next_payment_date

    def is_first_payment_pending(self):
        if self.first_payment_amount is None or float(self.first_payment_amount or 0) <= 0:
            return False
        return not any(
            (
                not getattr(payment, 'is_early_repayment', False)
                or float(getattr(payment, 'scheduled_payment_amount', 0) or 0) > 0
            )
            for payment in (self.payments or [])
        )

    def effective_next_payment_amount(self):
        if self.is_first_payment_pending():
            return self.first_payment_amount
        return self.minimum_payment

    def effective_planned_early_repayment_amount(self):
        if not self.early_repayment_enabled or self.planned_early_repayment_amount is None:
            return 0
        desired_total = self.planned_early_repayment_amount
        required_payment = self.minimum_payment or 0
        return max(desired_total - required_payment, 0)

    def to_dict(self):
        today = date.today()
        effective_next_payment_date = self.effective_next_payment_date(today)
        effective_next_payment_amount = self.effective_next_payment_amount()
        days_until_payment = None
        if effective_next_payment_date:
            days_until_payment = (effective_next_payment_date - today).days
        effective_interest_rate = self.interest_rate_for(today)

        return {
            'id': self.id,
            'bank_name': self.bank_name,
            'debt_type': self.debt_type,
            'debt_type_label': self.DEBT_TYPE_LABELS.get(self.debt_type, self.debt_type),
            'product_name': self.product_name,
            'total_amount': float(self.total_amount),
            'remaining_amount': float(self.remaining_amount),
            'minimum_payment': float(self.minimum_payment) if self.minimum_payment else None,
            'first_payment_amount': float(self.first_payment_amount) if self.first_payment_amount is not None else None,
            'is_first_payment_pending': self.is_first_payment_pending(),
            'effective_next_payment_amount': float(effective_next_payment_amount) if effective_next_payment_amount is not None else None,
            'interest_rate': float(self.interest_rate) if self.interest_rate else None,
            'interest_rate_after_change': float(self.interest_rate_after_change) if self.interest_rate_after_change else None,
            'interest_rate_change_date': self.interest_rate_change_date.strftime('%Y-%m-%d') if self.interest_rate_change_date else None,
            'interest_rate_change_date_display': self.interest_rate_change_date.strftime('%d.%m.%Y') if self.interest_rate_change_date else None,
            'effective_interest_rate': float(effective_interest_rate) if effective_interest_rate else None,
            'next_payment_date': self.next_payment_date.strftime('%Y-%m-%d') if self.next_payment_date else None,
            'next_payment_date_display': self.next_payment_date.strftime('%d.%m.%Y') if self.next_payment_date else None,
            'effective_next_payment_date': effective_next_payment_date.strftime('%Y-%m-%d') if effective_next_payment_date else None,
            'effective_next_payment_date_display': effective_next_payment_date.strftime('%d.%m.%Y') if effective_next_payment_date else None,
            'is_payment_recurring': self.is_payment_recurring,
            'repayment_type': self.repayment_type,
            'day_count_convention': self.day_count_convention,
            'include_payment_day': self.include_payment_day,
            'interest_period_start_date': self.interest_period_start_date.strftime('%Y-%m-%d') if self.interest_period_start_date else None,
            'interest_period_start_date_display': self.interest_period_start_date.strftime('%d.%m.%Y') if self.interest_period_start_date else None,
            'early_repayment_strategy': self.early_repayment_strategy,
            'early_repayment_enabled': bool(self.early_repayment_enabled),
            'planned_early_repayment_amount': float(self.planned_early_repayment_amount) if self.planned_early_repayment_amount else None,
            'effective_planned_early_repayment_amount': float(self.effective_planned_early_repayment_amount()),
            'loan_term_months': self.loan_term_months,
            'monthly_fee_amount': float(self.monthly_fee_amount or 0),
            'bank_remaining_amount': float(self.bank_remaining_amount) if self.bank_remaining_amount is not None else None,
            'bank_remaining_delta': float(self.bank_remaining_amount - self.remaining_amount) if self.bank_remaining_amount is not None else None,
            'comment': self.comment,
            'status': self.status,
            'days_until_payment': days_until_payment,
            'paid_percent': round((1 - float(self.remaining_amount) / float(self.total_amount)) * 100, 1) if float(self.total_amount) > 0 else 100,
            'created_at': self.created_at.strftime('%d.%m.%Y') if self.created_at else None,
        }

    def __repr__(self):
        return f'<Debt {self.bank_name} - {self.product_name}>'


class Payment(db.Model):
    __tablename__ = 'payments'
    __table_args__ = (
        db.Index('ix_payments_debt_date', 'debt_id', 'payment_date'),
    )

    id = db.Column(db.Integer, primary_key=True)
    debt_id = db.Column(db.Integer, db.ForeignKey('debts.id'), nullable=False)
    amount = db.Column(db.Numeric(12, 2), nullable=False)
    principal_amount = db.Column(db.Numeric(12, 2), nullable=False, default=0)
    interest_amount = db.Column(db.Numeric(12, 2), nullable=False, default=0)
    fee_amount = db.Column(db.Numeric(12, 2), nullable=False, default=0)
    payment_date = db.Column(db.Date, nullable=False, default=date.today)
    comment = db.Column(db.Text, nullable=True)
    is_early_repayment = db.Column(db.Boolean, default=False, nullable=False)
    scheduled_payment_amount = db.Column(db.Numeric(12, 2), nullable=True)
    remaining_after_payment = db.Column(db.Numeric(12, 2), nullable=False)
    bank_remaining_after_payment = db.Column(db.Numeric(12, 2), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        principal_amount = self.principal_amount if self.principal_amount is not None else self.amount
        interest_amount = self.interest_amount if self.interest_amount is not None else 0
        fee_amount = self.fee_amount if self.fee_amount is not None else 0
        scheduled_payment_amount = self.scheduled_payment_amount or 0
        early_repayment_amount = (
            max(self.amount - scheduled_payment_amount, 0)
            if self.is_early_repayment
            else 0
        )
        return {
            'id': self.id,
            'debt_id': self.debt_id,
            'amount': float(self.amount),
            'principal_amount': float(principal_amount),
            'interest_amount': float(interest_amount),
            'fee_amount': float(fee_amount),
            'payment_date': self.payment_date.strftime('%d.%m.%Y') if self.payment_date else None,
            'payment_date_iso': self.payment_date.strftime('%Y-%m-%d') if self.payment_date else None,
            'comment': self.comment,
            'is_early_repayment': self.is_early_repayment,
            'scheduled_payment_amount': float(scheduled_payment_amount),
            'early_repayment_amount': float(early_repayment_amount),
            'remaining_after_payment': float(self.remaining_after_payment),
            'bank_remaining_after_payment': float(self.bank_remaining_after_payment) if self.bank_remaining_after_payment is not None else None,
            'created_at': self.created_at.strftime('%d.%m.%Y %H:%M') if self.created_at else None,
        }

    def __repr__(self):
        return f'<Payment {self.amount} for debt {self.debt_id}>'


class SplitPurchase(db.Model):
    __tablename__ = 'split_purchases'

    id = db.Column(db.Integer, primary_key=True)
    debt_id = db.Column(db.Integer, db.ForeignKey('debts.id'), nullable=False)
    title = db.Column(db.String(150), nullable=True)
    amount = db.Column(db.Numeric(12, 2), nullable=False)
    purchase_date = db.Column(db.Date, nullable=False)
    installments_count = db.Column(db.Integer, nullable=False, default=4)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    debt = db.relationship('Debt', back_populates='split_purchases')

    def to_dict(self):
        return {
            'id': self.id,
            'debt_id': self.debt_id,
            'title': self.title,
            'amount': float(self.amount),
            'purchase_date': self.purchase_date.strftime('%Y-%m-%d') if self.purchase_date else None,
            'purchase_date_display': self.purchase_date.strftime('%d.%m.%Y') if self.purchase_date else None,
            'installments_count': self.installments_count,
            'created_at': self.created_at.strftime('%d.%m.%Y %H:%M') if self.created_at else None,
        }

    def __repr__(self):
        return f'<SplitPurchase {self.amount} for debt {self.debt_id}>'


class Income(db.Model):
    __tablename__ = 'incomes'
    __table_args__ = (
        db.Index('ix_incomes_user_date', 'user_id', 'income_date'),
    )

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    amount = db.Column(db.Numeric(12, 2), nullable=False)
    category = db.Column(db.Enum('salary', 'advance', 'side_job', 'debt_return', 'bonus', 'scholarship', 'vacation_pay', 'goal_withdrawal', 'other'), nullable=False)
    source = db.Column(db.String(150), nullable=True)
    income_date = db.Column(db.Date, nullable=False)
    comment = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    user = db.relationship('User', back_populates='incomes')

    def to_dict(self):
        return {
            'id': self.id,
            'amount': float(self.amount),
            'category': self.category,
            'source': self.source,
            'income_date': self.income_date.strftime('%Y-%m-%d') if self.income_date else None,
            'income_date_display': self.income_date.strftime('%d.%m.%Y') if self.income_date else None,
            'comment': self.comment,
            'created_at': self.created_at.strftime('%d.%m.%Y %H:%M') if self.created_at else None,
        }

    def __repr__(self):
        return f'<Income {self.amount} {self.category}>'


class Expense(db.Model):
    __tablename__ = 'expenses'
    __table_args__ = (
        db.Index('ix_expenses_user_date', 'user_id', 'expense_date'),
        db.Index(
            'uq_expenses_monthly_occurrence',
            'user_id',
            'monthly_group_id',
            'generated_for_month',
            unique=True,
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    amount = db.Column(db.Numeric(12, 2), nullable=False)
    category = db.Column(db.Enum('products', 'transport', 'communication', 'rent', 'loans', 'restaurants', 'entertainment', 'health', 'education', 'clothing', 'subscriptions', 'savings', 'other'), nullable=False)
    title = db.Column(db.String(150), nullable=False)
    expense_date = db.Column(db.Date, nullable=False)
    payment_method = db.Column(db.String(80), nullable=True)
    comment = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    # Поля для ежемесячных расходов
    is_monthly = db.Column(db.Boolean, default=False, nullable=False)
    monthly_group_id = db.Column(db.String(36), nullable=True)
    generated_from_id = db.Column(
        db.Integer,
        db.ForeignKey('expenses.id', ondelete='SET NULL'),
        nullable=True,
    )
    generated_for_month = db.Column(db.String(7), nullable=True)  # YYYY-MM формат

    user = db.relationship('User', back_populates='expenses')

    def to_dict(self):
        return {
            'id': self.id,
            'amount': float(self.amount),
            'category': self.category,
            'title': self.title,
            'expense_date': self.expense_date.strftime('%Y-%m-%d') if self.expense_date else None,
            'expense_date_display': self.expense_date.strftime('%d.%m.%Y') if self.expense_date else None,
            'payment_method': self.payment_method,
            'comment': self.comment,
            'created_at': self.created_at.strftime('%d.%m.%Y %H:%M') if self.created_at else None,
            'is_monthly': self.is_monthly,
            'monthly_group_id': self.monthly_group_id,
            'generated_from_id': self.generated_from_id,
            'generated_for_month': self.generated_for_month,
        }

    def __repr__(self):
        return f'<Expense {self.amount} {self.title}>'


class FinancialPlanPreference(db.Model):
    __tablename__ = 'financial_plan_preferences'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer,
        db.ForeignKey('users.id', ondelete='CASCADE'),
        nullable=False,
        unique=True,
    )
    living_minimum = db.Column(db.Numeric(12, 2), nullable=False, default=20000)
    desired_monthly_savings = db.Column(db.Numeric(12, 2), nullable=False, default=5000)
    emergency_fund_target_amount = db.Column(db.Numeric(12, 2), nullable=False, default=30000)
    emergency_fund_target_mode = db.Column(
        db.Enum('fixed', 'one_month', 'three_months'),
        nullable=False,
        default='fixed',
    )
    strategy = db.Column(
        db.Enum('safe', 'balanced', 'aggressive'),
        nullable=False,
        default='balanced',
    )
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    user = db.relationship('User', back_populates='financial_plan_preference')

    def __repr__(self):
        return f'<FinancialPlanPreference user={self.user_id} strategy={self.strategy}>'


class EmergencyFundTransaction(db.Model):
    __tablename__ = 'emergency_fund_transactions'
    __table_args__ = (
        db.CheckConstraint('amount > 0', name='ck_emergency_fund_transaction_amount_positive'),
        db.Index('ix_emergency_fund_transactions_user_date', 'user_id', 'transaction_date'),
    )

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='CASCADE'), nullable=False)
    transaction_type = db.Column(
        db.Enum('deposit', 'withdrawal'),
        nullable=False,
    )
    amount = db.Column(db.Numeric(12, 2), nullable=False)
    transaction_date = db.Column(db.Date, nullable=False, default=date.today)
    comment = db.Column(db.String(255), nullable=True)
    expense_id = db.Column(db.Integer, db.ForeignKey('expenses.id', ondelete='SET NULL'), nullable=True, unique=True)
    income_id = db.Column(db.Integer, db.ForeignKey('incomes.id', ondelete='SET NULL'), nullable=True, unique=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    user = db.relationship('User', back_populates='emergency_fund_transactions')
    expense = db.relationship('Expense', foreign_keys=[expense_id])
    income = db.relationship('Income', foreign_keys=[income_id])

    def __repr__(self):
        return f'<EmergencyFundTransaction user={self.user_id} {self.transaction_type} {self.amount}>'


class FinancialGoal(db.Model):
    __tablename__ = 'financial_goals'
    __table_args__ = (
        db.CheckConstraint('target_amount > 0', name='ck_financial_goal_target_positive'),
        db.CheckConstraint('monthly_contribution >= 0', name='ck_financial_goal_monthly_nonnegative'),
        db.Index('ix_financial_goals_user_priority', 'user_id', 'priority'),
    )

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='CASCADE'), nullable=False)
    name = db.Column(db.String(120), nullable=False)
    target_amount = db.Column(db.Numeric(12, 2), nullable=False)
    monthly_contribution = db.Column(db.Numeric(12, 2), nullable=False, default=0)
    target_date = db.Column(db.Date, nullable=True)
    note = db.Column(db.String(500), nullable=True)
    priority = db.Column(db.Integer, nullable=False, default=2)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    user = db.relationship('User', back_populates='financial_goals')
    transactions = db.relationship(
        'FinancialGoalTransaction',
        back_populates='goal',
        lazy=True,
        cascade='all, delete-orphan',
        order_by='FinancialGoalTransaction.transaction_date.desc()',
    )

    def __repr__(self):
        return f'<FinancialGoal user={self.user_id} {self.name}>'


class FinancialGoalTransaction(db.Model):
    __tablename__ = 'financial_goal_transactions'
    __table_args__ = (
        db.CheckConstraint('amount > 0', name='ck_financial_goal_transaction_amount_positive'),
        db.Index('ix_financial_goal_transactions_goal_date', 'goal_id', 'transaction_date'),
    )

    id = db.Column(db.Integer, primary_key=True)
    goal_id = db.Column(db.Integer, db.ForeignKey('financial_goals.id', ondelete='CASCADE'), nullable=False)
    transaction_type = db.Column(db.Enum('deposit', 'withdrawal'), nullable=False)
    amount = db.Column(db.Numeric(12, 2), nullable=False)
    transaction_date = db.Column(db.Date, nullable=False, default=date.today)
    comment = db.Column(db.String(255), nullable=True)
    expense_id = db.Column(db.Integer, db.ForeignKey('expenses.id', ondelete='SET NULL'), nullable=True, unique=True)
    income_id = db.Column(db.Integer, db.ForeignKey('incomes.id', ondelete='SET NULL'), nullable=True, unique=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    goal = db.relationship('FinancialGoal', back_populates='transactions')
    expense = db.relationship('Expense', foreign_keys=[expense_id])
    income = db.relationship('Income', foreign_keys=[income_id])

    def __repr__(self):
        return f'<FinancialGoalTransaction goal={self.goal_id} {self.transaction_type} {self.amount}>'


class AppSetting(db.Model):
    __tablename__ = 'app_settings'

    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(100), unique=True, nullable=False)
    value = db.Column(db.Text, nullable=True)
    description = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def __repr__(self):
        return f'<AppSetting {self.key}>'


class DictionaryEntry(db.Model):
    __tablename__ = 'dictionary_entries'
    __table_args__ = (
        db.UniqueConstraint('dictionary_type', 'value', name='uq_dictionary_type_value'),
    )

    id = db.Column(db.Integer, primary_key=True)
    dictionary_type = db.Column(db.Enum('bank', 'debt_type', 'debt_category', 'status', 'comment_template', 'interest_rate', 'product_type'), nullable=False)
    value = db.Column(db.String(150), nullable=False)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def __repr__(self):
        return f'<DictionaryEntry {self.dictionary_type}:{self.value}>'


class ActivityLog(db.Model):
    __tablename__ = 'activity_logs'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    action = db.Column(db.String(100), nullable=False)
    entity_type = db.Column(db.String(50), nullable=True)
    entity_id = db.Column(db.Integer, nullable=True)
    description = db.Column(db.Text, nullable=True)
    ip_address = db.Column(db.String(100), nullable=True)
    user_agent = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    user = db.relationship('User', back_populates='activity_logs')

    def __repr__(self):
        return f'<ActivityLog {self.action}>'


class TelegramProcessedUpdate(db.Model):
    __tablename__ = 'telegram_processed_updates'

    update_id = db.Column(db.BigInteger, primary_key=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    def __repr__(self):
        return f'<TelegramProcessedUpdate {self.update_id}>'


class TelegramConversationState(db.Model):
    __tablename__ = 'telegram_conversation_states'
    __table_args__ = (
        db.UniqueConstraint('telegram_id', name='uq_telegram_conversation_states_telegram_id'),
        db.Index('ix_telegram_conversation_states_telegram_id', 'telegram_id'),
        db.Index('ix_telegram_conversation_states_expires_at', 'expires_at'),
    )

    id = db.Column(db.Integer, primary_key=True)
    telegram_id = db.Column(db.BigInteger, nullable=False)
    chat_id = db.Column(db.BigInteger, nullable=False)
    flow = db.Column(db.String(30), nullable=False)
    step = db.Column(db.String(50), nullable=False)
    data = db.Column(db.Text, nullable=False, default='{}')
    expires_at = db.Column(db.DateTime, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    def __repr__(self):
        return f'<TelegramConversationState {self.telegram_id}:{self.flow}:{self.step}>'
