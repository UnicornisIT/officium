import io
import re
import unittest
import zipfile
from datetime import date
from decimal import Decimal
from unittest.mock import patch

from app import create_app
from app.models import Expense, User
from app.services.bank_statement_import_service import parse_bank_statement
from extensions import db


class BankStatementImportServiceTestCase(unittest.TestCase):
    def parse_csv(self, text, filename='statement.csv'):
        return parse_bank_statement(text.encode('cp1251'), filename)

    def test_parse_sber_signed_amount_statement(self):
        result = self.parse_csv(
            'Сбербанк Онлайн\n'
            'Дата операции;Описание операции;Сумма операции;Категория;Карта\n'
            '01.07.2026;MAGNIT;-1 250,50;Супермаркеты;*1234\n'
            '02.07.2026;Зарплата;50000;Переводы;*1234\n',
            'sber.csv',
        )

        self.assertEqual(result.bank, 'sber')
        self.assertEqual(len(result.rows), 1)
        self.assertEqual(result.skipped_income, 1)
        self.assertEqual(result.rows[0].amount, Decimal('1250.50'))
        self.assertEqual(result.rows[0].category, 'products')

    def test_positive_amount_with_expense_category_is_imported(self):
        result = self.parse_csv(
            'Сбербанк Онлайн\n'
            'Дата операции;Описание операции;Сумма операции;Категория;Карта\n'
            '01.07.2026;LENTA;1 250,50;Супермаркеты;*1234\n',
            'sber.csv',
        )

        self.assertEqual(len(result.rows), 1)
        self.assertEqual(result.rows[0].amount, Decimal('1250.50'))

    def test_parse_tbank_debit_credit_statement(self):
        result = self.parse_csv(
            'Т-Банк\n'
            'Дата операции;Описание;Расход;Приход;Категория;Номер карты\n'
            '03.07.2026;Яндекс Go;620;;Транспорт;*5678\n'
            '04.07.2026;Возврат;;120;Возвраты;*5678\n',
            'tbank.csv',
        )

        self.assertEqual(result.bank, 'tbank')
        self.assertEqual(len(result.rows), 1)
        self.assertEqual(result.rows[0].category, 'transport')
        self.assertEqual(result.rows[0].payment_method, 'card')

    def test_parse_alfabank_statement(self):
        result = self.parse_csv(
            'Альфа-Банк\n'
            'Дата;Описание;Сумма;Категория;Счет\n'
            '05.07.2026;Аптека 36.6;-980,00;Здоровье;40817\n',
            'alfa.csv',
        )

        self.assertEqual(result.bank, 'alfabank')
        self.assertEqual(result.rows[0].category, 'health')

    def test_parse_vtb_statement(self):
        result = self.parse_csv(
            'ВТБ\n'
            'Дата списания;Контрагент;Списание;Зачисление;Тип операции;Карта\n'
            '06.07.2026;МТС;750;;Связь;*9012\n',
            'vtb.csv',
        )

        self.assertEqual(result.bank, 'vtb')
        self.assertEqual(result.rows[0].category, 'communication')

    def test_parse_xlsx_statement(self):
        sheet_xml = '''<?xml version="1.0" encoding="UTF-8"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
  <sheetData>
    <row r="1">
      <c r="A1" t="inlineStr"><is><t>Дата операции</t></is></c>
      <c r="B1" t="inlineStr"><is><t>Описание операции</t></is></c>
      <c r="C1" t="inlineStr"><is><t>Сумма операции</t></is></c>
      <c r="D1" t="inlineStr"><is><t>Категория</t></is></c>
    </row>
    <row r="2">
      <c r="A2" t="inlineStr"><is><t>07.07.2026</t></is></c>
      <c r="B2" t="inlineStr"><is><t>Spotify</t></is></c>
      <c r="C2" t="inlineStr"><is><t>-399,00</t></is></c>
      <c r="D2" t="inlineStr"><is><t>Подписки</t></is></c>
    </row>
  </sheetData>
</worksheet>'''
        file_obj = io.BytesIO()
        with zipfile.ZipFile(file_obj, 'w') as archive:
            archive.writestr('xl/worksheets/sheet1.xml', sheet_xml)

        result = parse_bank_statement(file_obj.getvalue(), 'statement.xlsx')

        self.assertEqual(len(result.rows), 1)
        self.assertEqual(result.rows[0].category, 'subscriptions')
        self.assertEqual(result.rows[0].amount, Decimal('399.00'))

    def test_parse_sber_pdf_statement_text(self):
        pdf_text = (
            'СберБанк Онлайн\n'
            '01.07.2026 Покупка MAGNIT -1 250,50 ₽\n'
            '02.07.2026 Зачисление зарплаты 50 000,00 ₽\n'
            '03.07.2026 Оплата Яндекс Go 620,00 ₽\n'
        )

        with patch(
            'app.services.bank_statement_import_service._extract_pdf_text',
            return_value=pdf_text,
        ):
            result = parse_bank_statement(b'%PDF fake', 'sber.pdf')

        self.assertEqual(result.bank, 'sber')
        self.assertEqual(len(result.rows), 2)
        self.assertEqual(result.rows[0].amount, Decimal('1250.50'))
        self.assertEqual(result.rows[0].category, 'products')
        self.assertEqual(result.rows[1].category, 'transport')

    def test_parse_sber_credit_card_pdf_statement_blocks(self):
        pdf_text = (
            'СберБанк Онлайн\n'
            'Выписка по счёту кредитной карты\n'
            'Расшифровка операций\n'
            'ДАТА ОПЕРАЦИИ (МСК)\n'
            'Дата обработки¹\n'
            'и код авторизацииКАТЕГОРИЯ\n'
            'Описание операцииСУММА В РУБЛЯХ\n'
            '29.06.2026\n'
            '29.06.202617:45\n'
            '447019Прочие операции\n'
            'MOSCOW OTO*Telegram. Операция по карте ****8819299,00 100 671,26\n'
            '29.06.2026\n'
            '29.06.202608:57\n'
            '850458Перевод на карту\n'
            'Перевод от А. Эльдар Витальевич. Операция по карте\n'
            '****8819+7 000,00 100 970,26\n'
            '28.06.2026\n'
            '28.06.202617:13\n'
            '043229Автомобиль\n'
            'Stavropolskij AZS 26078. Операция по карте ****88191 539,00 93 970,26\n'
            '27.06.2026\n'
            '27.06.202615:49\n'
            '045338Рестораны и кафе\n'
            'Stavropol FAMILNAYA PEKARNYA. Операция по карте\n'
            '****8819918,00 96 985,26\n'
            '03.06.2026\n'
            '03.06.202614:28\n'
            '235901Автомобиль\n'
            'Stavropol EXPRESS 1 DOVATORTSEV. Операция по карте ****8819200,00 91 099,71\n'
            '09.06.2026\n'
            '09.06.202612:41\n'
            '449931Прочие расходы\n'
            'MOSCOW YANDEX*7372*OBLAKO. Операция по карте\n'
            '****88191 000,00 87 624,51\n'
            'Продолжение на следующей странице\n'
            'Индивидуальная выписка по счёту кредитной карты Страница 3 из 4\n'
            'ДАТА ОПЕРАЦИИ (МСК)\n'
            'Дата формирования документа 29.07.2026\n'
            '*\n'
            '40601D00C08FCE2CD999F93A68651986\n'
            'с 02.07.2025 по 02.10.2026\n'
            '29.07.2026\n'
            'ПАО Сбербанк. Генеральная лицензия Банка России № 1481 от 11.08.2015.\n'
        )

        with patch(
            'app.services.bank_statement_import_service._extract_pdf_text',
            return_value=pdf_text,
        ):
            result = parse_bank_statement(b'%PDF fake', 'sber.pdf')

        self.assertEqual(result.bank, 'sber')
        self.assertEqual(len(result.rows), 5)
        self.assertEqual(result.skipped_income, 1)
        self.assertEqual(result.errors, [])
        self.assertEqual(result.rows[0].amount, Decimal('299.00'))
        self.assertEqual(result.rows[0].title, 'Telegram')
        self.assertEqual(result.rows[0].category, 'subscriptions')
        self.assertEqual(result.rows[1].amount, Decimal('1539.00'))
        self.assertEqual(result.rows[1].title, 'AZS 26078')
        self.assertEqual(result.rows[1].category, 'transport')
        self.assertEqual(result.rows[2].title, 'FAMILNAYA PEKARNYA')
        self.assertEqual(result.rows[2].category, 'restaurants')
        self.assertEqual(result.rows[3].title, 'EXPRESS 1 DOVATORTSEV')
        self.assertEqual(result.rows[3].category, 'transport')
        self.assertEqual(result.rows[4].title, 'YANDEX OBLAKO')
        self.assertEqual(result.rows[4].category, 'subscriptions')

    def test_parse_tbank_movement_pdf_statement_blocks(self):
        pdf_text = (
            'АКЦИОНЕРНОЕ ОБЩЕСТВО «ТБАНК»\n'
            'Справка о движении средств\n'
            'Движение средств за период с 28.06.2026 по 28.07.2026\n'
            'Дата и время\n'
            'операцииДата\n'
            'списанияСумма в валюте\n'
            'операцииСумма операции\n'
            'в валюте картыОписание\n'
            'операцииНомер\n'
            'карты\n'
            '24.07.2026\n'
            '04:3924.07.2026\n'
            '05:48-1 166.00 ₽ -1 166.00 ₽ Оплата в YANDEX*4121*GO\n'
            'Moskva RUS5500\n'
            '21.07.2026\n'
            '09:5921.07.2026\n'
            '10:00-470.00 ₽ -470.00 ₽ Внешний перевод по\n'
            'номеру телефона\n'
            '+791876057109834\n'
            '20.07.2026\n'
            '18:5620.07.2026\n'
            '18:59-10 000.00 ₽ -10 000.00 ₽ Снятие наличных. Т-Банк,\n'
            '6586 Ставрополь Россия5500\n'
            '05.07.2026\n'
            '18:5507.07.2026\n'
            '16:34-6 135.66 ₽ -6 135.66 ₽ Оплата в AZS 24 Stavropol\n'
            'RUS5500\n'
            'АО «ТБанк» универсальная лицензия Банка России № 2673\n'
            '1\n'
            '420 042,97 ₽ Расходы:\n'
        )

        with patch(
            'app.services.bank_statement_import_service._extract_pdf_text',
            return_value=pdf_text,
        ):
            result = parse_bank_statement(b'%PDF fake', 'tbank.pdf')

        self.assertEqual(result.bank, 'tbank')
        self.assertEqual(len(result.rows), 4)
        self.assertEqual(result.rows[0].title, 'YANDEX GO')
        self.assertEqual(result.rows[0].category, 'transport')
        self.assertEqual(result.rows[1].payment_method, 'transfer')
        self.assertEqual(result.rows[1].title, 'Внешний перевод')
        self.assertEqual(result.rows[2].payment_method, 'cash')
        self.assertEqual(result.rows[2].title, 'Снятие наличных')
        self.assertEqual(result.rows[3].amount, Decimal('6135.66'))
        self.assertEqual(result.rows[3].title, 'AZS 24')
        self.assertEqual(result.rows[3].category, 'transport')


class BankStatementImportRouteTestCase(unittest.TestCase):
    def setUp(self):
        self.app = create_app({
            'TESTING': True,
            'SQLALCHEMY_DATABASE_URI': 'sqlite:///:memory:',
            'SQLALCHEMY_ENGINE_OPTIONS': {},
            'WTF_CSRF_ENABLED': False,
        })
        self.client = self.app.test_client()

        with self.app.app_context():
            db.create_all()
            self.user = User(
                telegram_id=777,
                username='import_user',
                first_name='Import',
                role='user',
            )
            db.session.add(self.user)
            db.session.commit()
            self.user_id = self.user.id

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()

    def login(self):
        with self.client.session_transaction() as session:
            session['_user_id'] = str(self.user_id)
            session['_fresh'] = True

    def test_import_preview_and_confirm_creates_expense(self):
        self.login()
        csv_text = (
            'Сбербанк Онлайн\n'
            'Дата операции;Описание операции;Сумма операции;Категория;Карта\n'
            '01.07.2026;PEREKRESTOK;-2100,00;Супермаркеты;*1234\n'
        )

        preview = self.client.post(
            '/expenses/import',
            data={
                'statement_file': (
                    io.BytesIO(csv_text.encode('cp1251')),
                    'sber.csv',
                )
            },
            content_type='multipart/form-data',
        )

        self.assertEqual(preview.status_code, 200)
        html = preview.get_data(as_text=True)
        token = re.search(r'name="import_token" value="([^"]+)"', html).group(1)
        self.assertIn('PEREKRESTOK', html)

        response = self.client.post('/expenses/import/confirm', data={
            'import_token': token,
            'row_count': '1',
            'action_0': 'create',
            'expense_date_0': '2026-07-01',
            'title_0': 'PEREKRESTOK',
            'category_0': 'products',
            'amount_0': '2100.00',
            'payment_method_0': 'card',
            'comment_0': 'Импорт выписки: Сбер',
        })

        self.assertEqual(response.status_code, 302)
        with self.app.app_context():
            expense = Expense.query.filter_by(user_id=self.user_id).one()
            self.assertEqual(expense.title, 'PEREKRESTOK')
            self.assertEqual(expense.amount, Decimal('2100.00'))
            self.assertEqual(expense.category, 'products')

    def test_duplicate_import_row_can_be_explicitly_selected(self):
        self.login()
        with self.app.app_context():
            db.session.add(Expense(
                user_id=self.user_id,
                amount=Decimal('2100.00'),
                category='products',
                title='PEREKRESTOK',
                expense_date=date(2026, 7, 1),
                payment_method='card',
            ))
            db.session.commit()

        csv_text = (
            'Сбербанк Онлайн\n'
            'Дата операции;Описание операции;Сумма операции;Категория;Карта\n'
            '01.07.2026;PEREKRESTOK;-2100,00;Супермаркеты;*1234\n'
        )

        preview = self.client.post(
            '/expenses/import',
            data={
                'statement_file': (
                    io.BytesIO(csv_text.encode('cp1251')),
                    'sber.csv',
                )
            },
            content_type='multipart/form-data',
        )

        self.assertEqual(preview.status_code, 200)
        html = preview.get_data(as_text=True)
        token = re.search(r'name="import_token" value="([^"]+)"', html).group(1)
        self.assertIn('дубликат', html)
        self.assertRegex(html, r'name="action_0"[\s\S]*value="skip" selected')

        response = self.client.post('/expenses/import/confirm', data={
            'import_token': token,
            'row_count': '1',
            'action_0': 'create',
            'expense_date_0': '2026-07-01',
            'title_0': 'PEREKRESTOK',
            'category_0': 'products',
            'amount_0': '2100.00',
            'payment_method_0': 'card',
            'comment_0': 'Импорт выписки: Сбер',
        })

        self.assertEqual(response.status_code, 302)
        with self.app.app_context():
            self.assertEqual(Expense.query.filter_by(user_id=self.user_id).count(), 2)

    def test_import_can_update_matching_monthly_expense(self):
        self.login()
        with self.app.app_context():
            monthly = Expense(
                user_id=self.user_id,
                amount=Decimal('750.00'),
                category='communication',
                title='Билайн',
                expense_date=date(2026, 7, 10),
                payment_method='card',
                comment='Плановая связь',
                is_monthly=True,
                monthly_group_id='beeline-monthly',
                generated_for_month='2026-07',
            )
            db.session.add(monthly)
            db.session.commit()
            monthly_id = monthly.id

        csv_text = (
            'Сбербанк Онлайн\n'
            'Дата операции;Описание операции;Сумма операции;Категория;Карта\n'
            '17.07.2026;oplata beeline;-900,00;Связь;*1234\n'
        )

        preview = self.client.post(
            '/expenses/import',
            data={
                'statement_file': (
                    io.BytesIO(csv_text.encode('cp1251')),
                    'sber.csv',
                )
            },
            content_type='multipart/form-data',
        )

        self.assertEqual(preview.status_code, 200)
        html = preview.get_data(as_text=True)
        token = re.search(r'name="import_token" value="([^"]+)"', html).group(1)
        self.assertIn('Ежемесячный: Билайн', html)
        self.assertRegex(html, r'name="action_0"[\s\S]*value="update_monthly" selected')

        response = self.client.post('/expenses/import/confirm', data={
            'import_token': token,
            'row_count': '1',
            'action_0': 'update_monthly',
            'expense_date_0': '2026-07-17',
            'title_0': 'oplata beeline',
            'category_0': 'communication',
            'amount_0': '900.00',
            'payment_method_0': 'card',
            'comment_0': 'Импорт выписки: Сбер',
        })

        self.assertEqual(response.status_code, 302)
        with self.app.app_context():
            expenses = Expense.query.filter_by(user_id=self.user_id).all()
            self.assertEqual(len(expenses), 1)
            monthly = db.session.get(Expense, monthly_id)
            self.assertEqual(monthly.title, 'Билайн')
            self.assertEqual(monthly.category, 'communication')
            self.assertEqual(monthly.amount, Decimal('900.00'))
            self.assertEqual(monthly.expense_date, date(2026, 7, 17))
            self.assertEqual(monthly.comment, 'Импорт выписки: Сбер')

    def test_only_best_same_monthly_match_updates_by_default(self):
        self.login()
        with self.app.app_context():
            db.session.add(Expense(
                user_id=self.user_id,
                amount=Decimal('750.00'),
                category='communication',
                title='Билайн',
                expense_date=date(2026, 7, 10),
                payment_method='card',
                is_monthly=True,
                monthly_group_id='beeline-monthly',
                generated_for_month='2026-07',
            ))
            db.session.commit()

        csv_text = (
            'Сбербанк Онлайн\n'
            'Дата операции;Описание операции;Сумма операции;Категория;Карта\n'
            '17.07.2026;oplata beeline;-750,00;Связь;*1234\n'
            '18.07.2026;oplata beeline;-300,00;Связь;*1234\n'
        )

        preview = self.client.post(
            '/expenses/import',
            data={
                'statement_file': (
                    io.BytesIO(csv_text.encode('cp1251')),
                    'sber.csv',
                )
            },
            content_type='multipart/form-data',
        )

        self.assertEqual(preview.status_code, 200)
        html = preview.get_data(as_text=True)
        self.assertEqual(html.count('value="update_monthly" selected'), 1)
        self.assertEqual(html.count('value="create" selected'), 1)


if __name__ == '__main__':
    unittest.main()
