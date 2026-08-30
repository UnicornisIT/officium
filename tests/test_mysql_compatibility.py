import unittest

from sqlalchemy import create_mock_engine

from app import _build_mysql_url, create_app
from extensions import db


class MySQLCompatibilityTestCase(unittest.TestCase):
    def test_mysql_url_escapes_credentials(self):
        url = _build_mysql_url({
            'DEV_SQLITE_COPY_SOURCE_URL': '',
            'DATABASE_URL': '',
            'DB_ENGINE': 'mysql',
            'DB_USER': 'user@tenant',
            'DB_PASSWORD': 'p:a/ss',
            'DB_HOST': 'db.internal',
            'DB_PORT': '3306',
            'DB_NAME': 'officium',
        })

        self.assertEqual(
            url,
            'mysql+pymysql://user%40tenant:p%3Aa%2Fss@db.internal:3306/officium?charset=utf8mb4',
        )

    def test_model_metadata_compiles_for_mysql(self):
        app = create_app({
            'TESTING': True,
            'SECRET_KEY': 'test-secret',
            'SQLALCHEMY_DATABASE_URI': 'sqlite:///:memory:',
            'SQLALCHEMY_ENGINE_OPTIONS': {},
        })
        statements = []
        engine = None

        def collect(statement, *multiparams, **params):
            statements.append(str(statement.compile(dialect=engine.dialect)))

        engine = create_mock_engine('mysql+pymysql://', collect)
        with app.app_context():
            db.metadata.create_all(engine)

        ddl = '\n'.join(statements)
        self.assertIn('CREATE TABLE users', ddl)
        self.assertIn('CREATE TABLE debts', ddl)
        self.assertIn('CREATE TABLE payments', ddl)
        self.assertIn('CREATE TABLE expenses', ddl)


if __name__ == '__main__':
    unittest.main()
