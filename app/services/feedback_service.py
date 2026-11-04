"""Приём обращений без дублирования текста в журнале действий."""

from uuid import UUID

from app.models import FeedbackReport
from app.utils import record_activity
from extensions import db


KINDS = {'idea', 'bug'}
STATUSES = {'new', 'in_progress', 'closed'}
MAX_BODY_LENGTH = 4000


def validate_body(body):
    body = str(body or '').strip()
    if not body or len(body) > MAX_BODY_LENGTH:
        raise ValueError('invalid_feedback_body')
    return body


def submit_report(user, kind, body, submission_key):
    if kind not in KINDS or user.is_blocked or not user.id:
        raise ValueError('invalid_feedback_sender_or_kind')
    body = validate_body(body)
    submission_key = UUID(str(submission_key)).hex
    existing = FeedbackReport.query.filter_by(
        submission_key=submission_key, user_id=user.id
    ).first()
    if existing:
        return existing
    report = FeedbackReport(
        user_id=user.id, kind=kind, body=body, submission_key=submission_key
    )
    db.session.add(report)
    db.session.flush()
    record_activity(
        'feedback_submit',
        user,
        entity_type='feedback',
        entity_id=report.id,
        description=kind,
        commit=False,
    )
    return report


def change_status(report, status, actor):
    if status not in STATUSES:
        raise ValueError('invalid_feedback_status')
    if report.status != status:
        report.status = status
        record_activity(
            'feedback_status_change',
            actor,
            entity_type='feedback',
            entity_id=report.id,
            description=status,
            commit=False,
        )
