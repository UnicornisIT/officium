import unittest
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from time import sleep
from unittest.mock import patch

from app.models import Debt
from app.services import debt_service


class DemoDebtConcurrencyTestCase(unittest.TestCase):
    def setUp(self):
        cache = patch.object(debt_service, '_demo_debts', None)
        cache.start()
        self.addCleanup(cache.stop)
        summary = patch(
            'app.services.finance_summary_service.get_finance_summary',
            side_effect=self.empty_summary,
        )
        self.summary = summary.start()
        self.addCleanup(summary.stop)

    @staticmethod
    def empty_summary(_user_id):
        sleep(0.005)
        return {'active_debts': []}

    def test_concurrent_creation_preserves_all_debts_and_unique_ids(self):
        debt_service.get_demo_debts()
        start = Barrier(20)

        def delayed_debt(**kwargs):
            # Widen the gap between ID allocation and append to expose the race.
            sleep(0.001)
            return Debt(**kwargs)

        def create_five(_worker):
            start.wait(timeout=10)
            for _ in range(5):
                debt_service.create_demo_debt(bank_name='Demo bank')

        with patch.object(debt_service, 'Debt', side_effect=delayed_debt):
            with ThreadPoolExecutor(max_workers=20) as executor:
                list(executor.map(create_five, range(20)))

        self.assertEqual(len(debt_service._demo_debts), 100)
        self.assertEqual(len({debt.id for debt in debt_service._demo_debts}), 100)

    def test_concurrent_first_access_initializes_cache_once(self):
        start = Barrier(20)

        def create_one(_worker):
            start.wait(timeout=10)
            return debt_service.create_demo_debt(bank_name='Demo bank')

        with ThreadPoolExecutor(max_workers=20) as executor:
            created = list(executor.map(create_one, range(20)))

        self.summary.assert_called_once_with(None)
        self.assertEqual(len(debt_service.get_demo_debts()), 20)
        self.assertEqual({debt.id for debt in created}, set(range(101, 121)))

    def test_list_is_a_snapshot(self):
        debt = debt_service.create_demo_debt(bank_name='Demo bank')
        snapshot = debt_service.get_demo_debts()
        snapshot.clear()

        self.assertIs(debt_service.get_demo_debt(debt.id), debt)
        self.assertTrue(debt_service.delete_demo_debt(debt.id))
        self.assertIsNone(debt_service.get_demo_debt(debt.id))
        self.assertFalse(debt_service.delete_demo_debt(debt.id))

    def test_concurrent_deletion_removes_each_debt_once(self):
        debt = debt_service.create_demo_debt(bank_name='Demo bank')
        start = Barrier(20)

        def delete_one(_worker):
            start.wait(timeout=10)
            return debt_service.delete_demo_debt(debt.id)

        with ThreadPoolExecutor(max_workers=20) as executor:
            deleted = list(executor.map(delete_one, range(20)))

        self.assertEqual(sum(deleted), 1)
        self.assertEqual(debt_service.get_demo_debts(), [])
