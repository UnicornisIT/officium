import unittest

from sqlalchemy import create_mock_engine

from app import create_app
from extensions import db


class MySQLCompatibilityTestCase(unittest.TestCase):
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
