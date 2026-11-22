import hashlib
import json
from unicodedata import normalize

from sqlalchemy import or_, update

from app.models import BankImportEntry, Expense, User
from app.services.bank_statement_import_service import BANK_LABELS
from extensions import db


def _digest(*parts):
    return hashlib.sha256(
        json.dumps(parts, ensure_ascii=False).encode('utf-8')
    ).hexdigest()


def _normalized(value):
    return ' '.join(normalize('NFKC', value).casefold().split())


def row_keys(row, statement_digest):
    source = (row.bank, _normalized(row.source_account))
    transaction_key = None
    if row.transaction_id and source != ('unknown', ''):
        transaction_key = _digest(source, row.transaction_id)
    return (
        _digest(statement_digest, row.row_number),
        transaction_key,
        _digest(
            source,
            row.expense_date.isoformat(),
            format(row.amount, '.2f'),
            _normalized(row.source_description or row.title),
        ),
    )


def lock_imports(user_id):
    # Authentication may have opened a read snapshot. Start with a write so
    # SQLite serializes writers and MySQL locks this user's row before any reads.
    db.session.rollback()
    db.session.execute(update(User).where(User.id == user_id).values(id=User.id))


def duplicate_statuses(user_id, keys):
    file_keys = {key[0] for key in keys if key}
    if not file_keys:
        return ['new'] * len(keys)
    transaction_keys = {key[1] for key in keys if key and key[1]}
    fallback_keys = {key[2] for key in keys if key}
    entries = db.session.query(
        BankImportEntry.file_row_key,
        BankImportEntry.transaction_key,
        BankImportEntry.fallback_key,
    ).filter(
        BankImportEntry.user_id == user_id,
        or_(
            BankImportEntry.file_row_key.in_(file_keys),
            BankImportEntry.transaction_key.in_(transaction_keys),
            BankImportEntry.fallback_key.in_(fallback_keys),
        ),
    )
    seen_files = set()
    seen_transactions = set()
    fallback_matches = {}
    for file_key, transaction_key, fallback_key in entries:
        seen_files.add(file_key)
        if transaction_key:
            seen_transactions.add(transaction_key)
        fallback_matches.setdefault(fallback_key, set()).add(transaction_key)

    statuses = []
    for key in keys:
        if not key:
            statuses.append('new')
            continue
        file_key, transaction_key, fallback_key = key
        matches = fallback_matches.get(fallback_key, set())
        if file_key in seen_files or transaction_key in seen_transactions:
            statuses.append('exact')
        elif matches and (transaction_key is None or None in matches):
            statuses.append('possible')
        else:
            statuses.append('new')
    return statuses


def legacy_match_exists(user_id, row):
    bank_label = BANK_LABELS[row.bank]
    return (
        db.session.query(Expense.id)
        .filter(
            Expense.user_id == user_id,
            Expense.expense_date == row.expense_date,
            Expense.amount == row.amount,
            Expense.title == row.title,
            Expense.id != row.monthly_match_id
            if isinstance(row.monthly_match_id, int)
            else True,
            ~db.session.query(BankImportEntry)
            .filter_by(expense_id=Expense.id)
            .exists(),
            or_(
                Expense.comment.is_(None),
                ~(
                    Expense.comment.like('Импорт выписки:%')
                    | Expense.comment.like('Импорт PDF-выписки:%')
                ),
                Expense.comment.like(f'Импорт выписки: {bank_label}%'),
                Expense.comment.like(f'Импорт PDF-выписки: {bank_label}%'),
            ),
        )
        .first()
        is not None
    )


def record_import(user_id, keys, expense):
    file_key, transaction_key, fallback_key = keys
    db.session.add(
        BankImportEntry(
            user_id=user_id,
            file_row_key=file_key,
            transaction_key=transaction_key,
            fallback_key=fallback_key,
            expense=expense,
        )
    )
