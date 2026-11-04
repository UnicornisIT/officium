import unittest
from uuid import uuid4

from app.models import ActivityLog, FeedbackReport
from app.services import feedback_service as feedback
from extensions import db
import test_personal_settings


class FeedbackServiceTests(unittest.TestCase):
    setUp = test_personal_settings.PersonalSettingsTests.setUp
    tearDown = test_personal_settings.PersonalSettingsTests.tearDown
    login = test_personal_settings.PersonalSettingsTests.login

    def test_submitted_report_is_idempotent_and_not_copied_to_audit(self):
        key = uuid4().hex
        report = feedback.submit_report(
            self.user, 'idea', 'Добавить семейный бюджет', key
        )
        db.session.commit()
        same = feedback.submit_report(
            self.user, 'idea', 'Добавить семейный бюджет', key
        )
        self.assertEqual(report.id, same.id)
        self.assertEqual(FeedbackReport.query.count(), 1)
        self.assertEqual(report.body, 'Добавить семейный бюджет')
        self.assertEqual(report.status, 'new')
        log = ActivityLog.query.filter_by(action='feedback_submit').one()
        self.assertEqual(log.description, 'idea')
        self.assertNotIn('семейный бюджет', log.description)

    def test_invalid_body_kind_and_blocked_sender_are_rejected(self):
        for body in ('', '   ', 'x' * 4001):
            with self.subTest(body_length=len(body)), self.assertRaises(ValueError):
                feedback.submit_report(self.user, 'bug', body, uuid4().hex)
        with self.assertRaises(ValueError):
            feedback.submit_report(self.user, 'other', 'Текст', uuid4().hex)
        self.user.is_blocked = True
        with self.assertRaises(ValueError):
            feedback.submit_report(self.user, 'bug', 'Текст', uuid4().hex)
        self.assertEqual(FeedbackReport.query.count(), 0)

    def test_status_change_is_validated_and_audited(self):
        report = feedback.submit_report(
            self.user, 'bug', 'Не открывается гараж', uuid4().hex
        )
        feedback.change_status(report, 'in_progress', self.other)
        feedback.change_status(report, 'in_progress', self.other)
        db.session.commit()
        self.assertEqual(report.status, 'in_progress')
        self.assertEqual(
            ActivityLog.query.filter_by(action='feedback_status_change').count(), 1
        )
        with self.assertRaises(ValueError):
            feedback.change_status(report, 'invalid', self.other)

    def test_user_deletion_preserves_report_without_author(self):
        report = feedback.submit_report(
            self.user, 'idea', 'Добавить экспорт', uuid4().hex
        )
        db.session.commit()
        db.session.delete(self.user)
        db.session.commit()
        db.session.refresh(report)
        self.assertIsNone(report.user_id)
        self.assertIsNone(report.user)
