from datetime import datetime, date
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

    debts = db.relationship('Debt', back_populates='user', lazy=True, cascade='all, delete-orphan')
    incomes = db.relationship('Income', back_populates='user', lazy=True, cascade='all, delete-orphan')
    expenses = db.relationship('Expense', back_populates='user', lazy=True, cascade='all, delete-orphan')
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

    id = db.Column(db.Integer, primary_key=True)
    bank_name = db.Column(db.String(100), nullable=False)
    debt_type = db.Column(db.Enum('credit_card', 'split', 'mortgage'), nullable=False)
    product_name = db.Column(db.String(150), nullable=False)
    total_amount = db.Column(db.Numeric(12, 2), nullable=False)
    remaining_amount = db.Column(db.Numeric(12, 2), nullable=False)
    minimum_payment = db.Column(db.Numeric(12, 2), nullable=True)
    interest_rate = db.Column(db.Numeric(5, 2), nullable=True)
    next_payment_date = db.Column(db.Date, nullable=True)
    comment = db.Column(db.Text, nullable=True)
    status = db.Column(db.Enum('active', 'archived'), nullable=False, default='active')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    user = db.relationship('User', back_populates='debts')
    payments = db.relationship('Payment', backref='debt', lazy=True, cascade='all, delete-orphan')

    def to_dict(self):
        today = date.today()
        days_until_payment = None
        if self.next_payment_date:
            days_until_payment = (self.next_payment_date - today).days

        return {
            'id': self.id,
            'bank_name': self.bank_name,
            'debt_type': self.debt_type,
            'debt_type_label': (
                'Кредитная карта' if self.debt_type == 'credit_card' else
                'Ипотека' if self.debt_type == 'mortgage' else
                'Сплит'
            ),
            'product_name': self.product_name,
            'total_amount': float(self.total_amount),
            'remaining_amount': float(self.remaining_amount),
            'minimum_payment': float(self.minimum_payment) if self.minimum_payment else None,
            'interest_rate': float(self.interest_rate) if self.interest_rate else None,
            'next_payment_date': self.next_payment_date.strftime('%Y-%m-%d') if self.next_payment_date else None,
            'next_payment_date_display': self.next_payment_date.strftime('%d.%m.%Y') if self.next_payment_date else None,
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

    id = db.Column(db.Integer, primary_key=True)
    debt_id = db.Column(db.Integer, db.ForeignKey('debts.id'), nullable=False)
    amount = db.Column(db.Numeric(12, 2), nullable=False)
    payment_date = db.Column(db.Date, nullable=False, default=date.today)
    comment = db.Column(db.Text, nullable=True)
    remaining_after_payment = db.Column(db.Numeric(12, 2), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            'id': self.id,
            'debt_id': self.debt_id,
            'amount': float(self.amount),
            'payment_date': self.payment_date.strftime('%d.%m.%Y') if self.payment_date else None,
            'comment': self.comment,
            'remaining_after_payment': float(self.remaining_after_payment),
            'created_at': self.created_at.strftime('%d.%m.%Y %H:%M') if self.created_at else None,
        }

    def __repr__(self):
        return f'<Payment {self.amount} for debt {self.debt_id}>'


class Income(db.Model):
    __tablename__ = 'incomes'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    amount = db.Column(db.Numeric(12, 2), nullable=False)
    category = db.Column(db.Enum('salary', 'advance', 'side_job', 'debt_return', 'bonus', 'scholarship', 'other'), nullable=False)
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

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    amount = db.Column(db.Numeric(12, 2), nullable=False)
    category = db.Column(db.Enum('products', 'transport', 'communication', 'rent', 'loans', 'entertainment', 'health', 'education', 'clothing', 'subscriptions', 'other'), nullable=False)
    title = db.Column(db.String(150), nullable=False)
    expense_date = db.Column(db.Date, nullable=False)
    payment_method = db.Column(db.String(80), nullable=True)
    comment = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

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
        }

    def __repr__(self):
        return f'<Expense {self.amount} {self.title}>'


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
