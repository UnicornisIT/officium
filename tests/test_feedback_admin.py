import unittest
from uuid import uuid4

from app.models import FeedbackReport
from app.services import feedback_service as feedback
from extensions import db
import test_personal_settings


class FeedbackAdminTests(unittest.TestCase):
    tearDown = test_personal_settings.PersonalSettingsTests.tearDown
    login = test_personal_settings.PersonalSettingsTests.login

    def setUp(self):
        test_personal_settings.PersonalSettingsTests.setUp(self)
        self.other.role = 'admin'
        self.google.role = 'superadmin'
        self.idea = feedback.submit_report(
            self.user,
            'idea',
            'Идея: <script>alert(1)</script>\nВторая строка',
            uuid4().hex,
        )
        self.bug = feedback.submit_report(
            self.user, 'bug', 'Ошибка в расходах', uuid4().hex
        )
        db.session.commit()

    def test_regular_user_and_anonymous_cannot_read_or_change_reports(self):
        for logged_in in (True, False):
            if not logged_in:
                self.client.get('/logout')
            self.assertEqual(self.client.get('/admin/feedback').status_code, 302)
            response = self.client.post(
                f'/admin/feedback/{self.idea.id}/status', data={'status': 'closed'}
            )
            self.assertEqual(response.status_code, 302)
        db.session.refresh(self.idea)
        self.assertEqual(self.idea.status, 'new')

    def test_both_admin_roles_can_read_filter_and_update_status(self):
        for user in (self.other, self.google):
            with self.subTest(role=user.role):
                self.bug.status = 'new'
                db.session.commit()
                self.login(user)
                html = self.client.get('/admin/feedback').get_data(as_text=True)
                self.assertIn('Обращения', html)
                self.assertIn('@alice', html)
                self.assertIn('&lt;script&gt;', html)
                self.assertNotIn('<script>alert(1)</script>', html)
                self.assertIn('Вторая строка', html)
                bugs = self.client.get('/admin/feedback?kind=bug').get_data(
                    as_text=True
                )
                self.assertIn('Ошибка в расходах', bugs)
                self.assertNotIn('Вторая строка', bugs)
                response = self.client.post(
                    f'/admin/feedback/{self.bug.id}/status', data={'status': 'closed'}
                )
                self.assertEqual(response.status_code, 302)
                self.assertNotIn(
                    'Ошибка в расходах',
                    self.client.get('/admin/feedback').get_data(as_text=True),
                )
                self.assertIn(
                    'Ошибка в расходах',
                    self.client.get('/admin/feedback?status=closed').get_data(
                        as_text=True
                    ),
                )

    def test_invalid_status_missing_report_and_csrf(self):
        self.login(self.other)
        self.assertEqual(
            self.client.get('/admin/feedback?status=invalid').status_code, 400
        )
        self.assertEqual(
            self.client.post(
                f'/admin/feedback/{self.idea.id}/status', data={'status': 'invalid'}
            ).status_code,
            400,
        )
        self.assertEqual(
            self.client.post(
                '/admin/feedback/999999/status', data={'status': 'closed'}
            ).status_code,
            404,
        )
        self.app.config['WTF_CSRF_ENABLED'] = True
        self.assertEqual(
            self.client.post(
                f'/admin/feedback/{self.idea.id}/status', data={'status': 'closed'}
            ).status_code,
            400,
        )
        db.session.refresh(self.idea)
        self.assertEqual(self.idea.status, 'new')

    def test_pagination_does_not_drop_filters(self):
        self.login(self.other)
        for index in range(21):
            feedback.submit_report(self.user, 'bug', f'Ошибка {index}', uuid4().hex)
        db.session.commit()
        response = self.client.get('/admin/feedback?status=new&kind=bug')
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn('page=2', html)
        self.assertIn('kind=bug', html)
        self.assertIn('status=new', html)
        self.assertNotIn('Вторая строка', html)
        self.assertEqual(FeedbackReport.query.count(), 23)
