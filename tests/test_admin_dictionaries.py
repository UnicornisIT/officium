import unittest

from app import create_app
from app.models import ActivityLog, DictionaryEntry, User
from app.utils import DICTIONARY_TYPES, normalize_dictionary_value
from extensions import db


class DictionaryValidationTestCase(unittest.TestCase):
    def test_only_types_used_by_the_debt_form_remain(self):
        self.assertEqual(
            [key for key, _ in DICTIONARY_TYPES],
            ['bank', 'product_type', 'interest_rate', 'comment_template'],
        )
        for removed in ('debt_type', 'debt_category', 'status'):
            with self.assertRaises(ValueError):
                normalize_dictionary_value(removed, 'x')

    def test_interest_rates_are_normalised_numbers(self):
        self.assertEqual(normalize_dictionary_value('interest_rate', '18,50'), '18.5')
        self.assertEqual(normalize_dictionary_value('interest_rate', '20 %'), '20')
        for bad in ('восемнадцать', '-1', '250', 'nan'):
            with self.subTest(value=bad), self.assertRaises(ValueError):
                normalize_dictionary_value('interest_rate', bad)

    def test_text_values_are_trimmed_and_limited(self):
        self.assertEqual(normalize_dictionary_value('bank', '  Т-Банк   онлайн '), 'Т-Банк онлайн')
        with self.assertRaises(ValueError):
            normalize_dictionary_value('bank', '   ')
        with self.assertRaises(ValueError):
            normalize_dictionary_value('bank', 'x' * 151)


class AdminDictionariesTestCase(unittest.TestCase):
    def setUp(self):
        self.app = create_app({
            'TESTING': True,
            'ENVIRONMENT': 'testing',
            'SECRET_KEY': 'dictionaries-secret',
            'SQLALCHEMY_DATABASE_URI': 'sqlite:///:memory:',
            'SQLALCHEMY_ENGINE_OPTIONS': {},
            'WTF_CSRF_ENABLED': False,
        })
        self.admin_client = self.app.test_client()
        self.user_client = self.app.test_client()
        with self.app.app_context():
            db.create_all()
            root = User(telegram_id=1, username='root', role='superadmin')
            member = User(telegram_id=2, username='member', role='user')
            db.session.add_all([root, member])
            db.session.commit()
            ids = (root.id, member.id)
        for client, user_id in zip((self.admin_client, self.user_client), ids):
            with client.session_transaction() as session:
                session['_user_id'] = str(user_id)
                session['_fresh'] = True

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()

    def _add(self, dictionary_type, value):
        return self.admin_client.post('/admin/dictionaries', data={'dictionary_type': dictionary_type, 'value': value})

    def _entry(self, value):
        with self.app.app_context():
            return DictionaryEntry.query.filter_by(value=value).first()

    def test_values_appear_as_hints_in_the_debt_form(self):
        self._add('bank', 'Банк Подсказка')
        self._add('product_type', 'Кредитная карта Плюс')
        self._add('interest_rate', '19,9')
        self._add('comment_template', 'Платить с зарплатной карты')

        html = self.user_client.get('/').get_data(as_text=True)

        self.assertIn('<option value="Банк Подсказка">', html)
        self.assertIn('<datalist id="productOptions"><option value="Кредитная карта Плюс">', html)
        self.assertIn('<datalist id="interestRateOptions"><option value="19.9">', html)
        self.assertIn('data-comment-template="Платить с зарплатной карты"', html)

    def test_invalid_and_duplicate_values_are_rejected(self):
        self._add('bank', 'Банк Один')

        duplicate = self._add('bank', 'банк один').get_data(as_text=True)
        bad_rate = self._add('interest_rate', 'много').get_data(as_text=True)
        removed_type = self._add('status', 'активен').get_data(as_text=True)

        self.assertIn('Такое значение уже есть', duplicate)
        self.assertIn('Ставка должна быть числом', bad_rate)
        self.assertIn('Выберите тип справочника', removed_type)
        with self.app.app_context():
            self.assertEqual(DictionaryEntry.query.count(), 1)

    def test_disabled_entry_is_kept_but_not_offered(self):
        self._add('bank', 'Банк Пауза')
        entry_id = self._entry('Банк Пауза').id

        self.admin_client.post(f'/admin/dictionaries/{entry_id}/update', data={'action': 'toggle'})

        self.assertFalse(self._entry('Банк Пауза').is_active)
        self.assertNotIn('Банк Пауза', self.user_client.get('/').get_data(as_text=True))
        self.assertIn('Банк Пауза', self.admin_client.get('/admin/dictionaries').get_data(as_text=True))

        self.admin_client.post(f'/admin/dictionaries/{entry_id}/update', data={'action': 'toggle'})
        self.assertTrue(self._entry('Банк Пауза').is_active)

    def test_entry_can_be_renamed_and_change_is_logged(self):
        self._add('bank', 'Старое имя')
        entry_id = self._entry('Старое имя').id

        self.admin_client.post(f'/admin/dictionaries/{entry_id}/update', data={'action': 'rename', 'value': 'Новое имя'})

        self.assertIsNone(self._entry('Старое имя'))
        self.assertIsNotNone(self._entry('Новое имя'))
        with self.app.app_context():
            log = ActivityLog.query.filter_by(action='Изменил элемент справочника').one()
            self.assertEqual(log.description, 'Старое имя → Новое имя')

    def test_admin_page_explains_each_dictionary(self):
        html = self.admin_client.get('/admin/dictionaries').get_data(as_text=True)

        self.assertIn('Подсказки в поле «Ставка, %» формы долга', html)
        self.assertIn('Кнопки быстрой вставки текста в комментарий к долгу', html)
        self.assertNotIn('Категории долгов', html)


if __name__ == '__main__':
    unittest.main()
