import unittest
from datetime import date

from app.services.pay_period_service import (
    main_payment_dates,
    next_income_after,
    parse_pay_period_mode,
    recommend_income_allocation,
    window_daily_limit,
)


OCTOBER = date(2026, 10, 1)
NOVEMBER = date(2026, 11, 1)


def income(income_id, day, amount, category='salary', source='Работа', mode=None):
    return {
        'id': income_id,
        'amount': amount,
        'category': category,
        'category_label': category,
        'source': source,
        'date': day,
        'pay_period_mode': mode,
    }


def obligation(label, day, amount, kind='expense'):
    return {
        'kind': kind,
        'icon': 'bi-repeat',
        'label': label,
        'detail': f'{label} · срок',
        'base_detail': label,
        'amount': amount,
        'due_date': day,
    }


def pool(label, amount, kind='savings'):
    return {
        'kind': kind,
        'icon': 'bi-shield-check',
        'label': label,
        'detail': 'план месяца',
        'base_detail': 'план месяца',
        'amount': amount,
    }


def history(day, category='salary', mode=None):
    return {'date': day, 'category': category, 'pay_period_mode': mode}


def by_date(result):
    return {item['date']: item for item in result['allocations']}


def directions(allocation):
    return [(row['label'], row['amount'], row['stage']) for row in allocation['allocations']]


class NextIncomeTestCase(unittest.TestCase):
    def test_recorded_next_payment_comes_first(self):
        dates = {date(2026, 10, 5), date(2026, 10, 20)}
        self.assertEqual(next_income_after(date(2026, 10, 5), dates), (date(2026, 10, 20), False))

    def test_without_a_recorded_one_the_next_payment_is_a_month_after_a_known_one(self):
        dates = {date(2026, 9, 20), date(2026, 10, 5), date(2026, 10, 9)}
        self.assertEqual(next_income_after(date(2026, 10, 9), dates), (date(2026, 10, 20), True))
        self.assertEqual(next_income_after(date(2026, 10, 5), dates), (date(2026, 10, 9), False))
        self.assertEqual(next_income_after(date(2026, 10, 5), set()), (None, False))

    def test_main_payments_follow_category_and_manual_mark(self):
        incomes = [
            income(1, date(2026, 10, 5), 1000),
            income(2, date(2026, 10, 12), 1000, 'bonus'),
            income(3, date(2026, 10, 15), 1000, 'other', mode='start'),
            income(4, date(2026, 10, 20), 1000, 'advance', mode='join'),
        ]
        self.assertEqual(main_payment_dates(incomes), {date(2026, 10, 5), date(2026, 10, 15)})

    def test_without_main_payments_every_income_counts(self):
        incomes = [income(1, date(2026, 10, 3), 1000, 'side_job'), income(2, date(2026, 10, 18), 1000, 'other')]
        self.assertEqual(main_payment_dates(incomes), {date(2026, 10, 3), date(2026, 10, 18)})

    def test_manual_mark_is_validated(self):
        self.assertIsNone(parse_pay_period_mode(''))
        self.assertEqual(parse_pay_period_mode('start'), 'start')
        with self.assertRaises(ValueError):
            parse_pay_period_mode('weekly')


class RecommendIncomeAllocationTestCase(unittest.TestCase):
    def assert_fully_directed(self, result):
        for item in result['allocations']:
            self.assertAlmostEqual(sum(row['amount'] for row in item['allocations']), item['amount'], places=2)
            self.assertAlmostEqual(item['allocated_total'], item['amount'], places=2)

    def test_income_pays_dates_until_next_income_then_reserve_then_ahead(self):
        result = recommend_income_allocation(
            [income(1, date(2026, 10, 5), 30000), income(2, date(2026, 10, 20), 25000, 'advance')],
            [
                obligation('Связь', date(2026, 10, 8), 1000),
                obligation('Аренда', date(2026, 10, 25), 6000),
                obligation('Кредит', date(2026, 11, 10), 5000, 'debt'),
            ],
            OCTOBER,
            living_minimum=31000,
        )
        salary = by_date(result)[date(2026, 10, 5)]
        self.assertEqual(salary['next_income_date'], date(2026, 10, 20))
        self.assertFalse(salary['next_income_is_estimated'])
        # 31 000 a month in October is 1 000 a day; 05.10-20.10 is 15 days.
        self.assertEqual(salary['reserve'], 15000.0)
        self.assertEqual(directions(salary), [
            ('Связь', 1000.0, 'due'),
            ('Аренда', 6000.0, 'ahead'),
            ('Повседневные расходы', 23000.0, 'living'),
        ])
        self.assertIn('на жизнь до 20.10.2026', salary['allocations'][-1]['detail'])
        self.assertIn('из них минимум на жизнь 15', salary['allocations'][-1]['detail'])
        self.assertIn('можно оплатить заранее', salary['allocations'][1]['detail'])
        # The credit of 10.11 is more than a month after 05.10: the advance pays
        # it after its own reserve until ~05.11 (16 days, 16 000).
        advance = by_date(result)[date(2026, 10, 20)]
        self.assertEqual(advance['next_income_date'], date(2026, 11, 5))
        self.assertEqual(directions(advance), [
            ('Кредит', 5000.0, 'ahead'), ('Повседневные расходы', 20000.0, 'living'),
        ])
        self.assertEqual(result['shortfalls'], [])
        self.assert_fully_directed(result)
        self.assertEqual(result['allocations'][0]['date'], date(2026, 10, 20))

    def test_reserve_comes_before_paying_ahead(self):
        result = recommend_income_allocation(
            [income(1, date(2026, 10, 5), 18000), income(2, date(2026, 10, 20), 8000, 'advance')],
            [obligation('Связь', date(2026, 10, 8), 1000), obligation('Аренда', date(2026, 10, 25), 6000)],
            OCTOBER,
            living_minimum=31000,
        )
        salary = by_date(result)[date(2026, 10, 5)]
        self.assertEqual(directions(salary), [
            ('Связь', 1000.0, 'due'),
            ('Аренда', 2000.0, 'ahead'),
            ('Повседневные расходы', 15000.0, 'living'),
        ])
        advance = by_date(result)[date(2026, 10, 20)]
        self.assertEqual(directions(advance)[0], ('Аренда', 4000.0, 'due'))
        self.assert_fully_directed(result)

    def test_later_income_pays_an_uncovered_date_before_it(self):
        result = recommend_income_allocation(
            [income(1, date(2026, 10, 5), 1000), income(2, date(2026, 10, 20), 9000, 'advance')],
            [obligation('Связь', date(2026, 10, 8), 3000)],
            OCTOBER,
        )
        salary = by_date(result)[date(2026, 10, 5)]
        self.assertEqual(directions(salary), [('Связь', 1000.0, 'due')])
        advance = by_date(result)[date(2026, 10, 20)]
        self.assertEqual(directions(advance)[0], ('Связь', 2000.0, 'overdue'))
        self.assertEqual(advance['allocations'][0]['urgency'], 'overdue')
        self.assertIn('срок уже наступил: 08.10.2026', advance['allocations'][0]['detail'])
        self.assertEqual(result['shortfalls'], [])

    def test_uncovered_date_and_missing_reserve_are_warned_on_the_card(self):
        result = recommend_income_allocation(
            [income(1, date(2026, 10, 5), 3000)],
            [obligation('Связь', date(2026, 10, 8), 2000), obligation('Ремонт', date(2026, 10, 12), 5000)],
            OCTOBER,
            history_incomes=[history(date(2026, 9, 5))],
            living_minimum=31000,
        )
        salary = result['allocations'][0]
        self.assertEqual(salary['next_income_date'], date(2026, 11, 5))
        self.assertTrue(salary['next_income_is_estimated'])
        self.assertEqual(
            [(item['label'], item['amount'], item['reason']) for item in salary['shortfalls']],
            [('Ремонт', 4000.0, 'insufficient')],
        )
        self.assertEqual(result['shortfalls'], salary['shortfalls'])
        self.assertEqual(salary['reserve_needed'], 31000.0)
        self.assertEqual(salary['reserve_missing'], 31000.0)
        self.assertEqual(result['reserve_shortfall'], 31000.0)
        self.assert_fully_directed(result)

    def test_dates_after_the_last_known_next_income_wait_for_it(self):
        result = recommend_income_allocation(
            [income(1, date(2026, 10, 5), 3000)],
            [obligation('Аренда', date(2026, 10, 25), 4000), obligation('Кредит', date(2026, 11, 7), 3000, 'debt')],
            OCTOBER,
            history_incomes=[history(date(2026, 9, 20), 'advance'), history(date(2026, 9, 5))],
        )
        salary = result['allocations'][0]
        self.assertEqual(salary['next_income_date'], date(2026, 10, 20))
        self.assertEqual(directions(salary), [('Аренда', 3000.0, 'ahead')])
        self.assertEqual(
            [
                (item['label'], item['amount'], item['prepaid'], item['expected_income_date'])
                for item in result['awaiting']
            ],
            [('Аренда', 1000.0, 3000.0, date(2026, 10, 20)), ('Кредит', 3000.0, 0.0, date(2026, 10, 20))],
        )
        self.assertTrue(all(item['expected_income_is_estimated'] for item in result['awaiting']))
        self.assertEqual(result['shortfalls'], [])

    def test_two_jobs_close_together_share_the_load(self):
        result = recommend_income_allocation(
            [
                income(1, date(2026, 10, 5), 2000, source='Первая работа'),
                income(2, date(2026, 10, 9), 5000, source='Вторая работа'),
            ],
            [obligation('Связь', date(2026, 10, 6), 500), obligation('Аренда', date(2026, 10, 15), 4000)],
            OCTOBER,
            history_incomes=[history(date(2026, 9, 20), 'advance')],
            living_minimum=31000,
        )
        first = by_date(result)[date(2026, 10, 5)]
        self.assertEqual(first['next_income_date'], date(2026, 10, 9))
        self.assertEqual(first['reserve_needed'], 4000.0)
        self.assertEqual(directions(first), [('Связь', 500.0, 'due'), ('Повседневные расходы', 1500.0, 'living')])
        self.assertEqual(first['reserve_missing'], 2500.0)
        second = by_date(result)[date(2026, 10, 9)]
        self.assertEqual(second['next_income_date'], date(2026, 10, 20))
        self.assertTrue(second['next_income_is_estimated'])
        self.assertEqual(directions(second), [('Аренда', 4000.0, 'due'), ('Повседневные расходы', 1000.0, 'living')])
        self.assert_fully_directed(result)

    def test_one_off_income_adds_to_the_reserve_already_kept(self):
        result = recommend_income_allocation(
            [income(1, date(2026, 10, 5), 15000), income(2, date(2026, 10, 12), 3000, 'bonus')],
            [obligation('Ремонт', date(2026, 10, 14), 2000)],
            OCTOBER,
            history_incomes=[history(date(2026, 9, 20), 'advance')],
            living_minimum=31000,
        )
        salary = by_date(result)[date(2026, 10, 5)]
        self.assertEqual(salary['next_income_date'], date(2026, 10, 20))
        self.assertEqual(directions(salary), [('Ремонт', 2000.0, 'due'), ('Повседневные расходы', 13000.0, 'living')])
        bonus = by_date(result)[date(2026, 10, 12)]
        # The salary kept 13 000 of the 15 000 reserve; the bonus tops it up.
        self.assertEqual(bonus['reserve_needed'], 2000.0)
        self.assertEqual(directions(bonus), [('Повседневные расходы', 3000.0, 'living')])
        self.assertEqual(salary['reserve_missing'], 0.0)
        self.assertEqual(result['reserve_shortfall'], 0.0)

    def test_missing_reserve_of_a_span_is_warned_once_on_the_latest_income(self):
        result = recommend_income_allocation(
            [income(1, date(2026, 10, 9), 1000), income(2, date(2026, 10, 12), 500, 'bonus')],
            [obligation('Ремонт', date(2026, 10, 14), 1500)],
            OCTOBER,
            history_incomes=[history(date(2026, 9, 20), 'advance')],
            living_minimum=31000,
        )
        salary = by_date(result)[date(2026, 10, 9)]
        bonus = by_date(result)[date(2026, 10, 12)]
        self.assertEqual(salary['reserve_missing'], 0.0)
        # 09.10-20.10 is 11 days of 1 000; nothing was kept for it.
        self.assertEqual(bonus['reserve_missing'], 11000.0)
        self.assertEqual(result['reserve_shortfall'], 11000.0)

    def test_dates_before_the_first_income_are_covered_by_the_previous_month(self):
        result = recommend_income_allocation(
            [income(1, date(2026, 10, 5), 5000)],
            [obligation('Музыка', date(2026, 10, 4), 169), obligation('Связь', date(2026, 10, 8), 500)],
            OCTOBER,
            history_incomes=[history(date(2026, 9, 20), 'advance')],
        )
        self.assertEqual(
            [(item['label'], item['reason'], item['expected_income_date']) for item in result['covered_by_previous']],
            [('Музыка', 'previous_period', date(2026, 9, 20))],
        )
        self.assertEqual([row['label'] for row in result['allocations'][0]['allocations']][0], 'Связь')
        self.assertEqual(result['shortfalls'], [])

    def test_without_previous_income_the_first_income_pays_earlier_dates(self):
        result = recommend_income_allocation(
            [income(1, date(2026, 10, 15), 5000)],
            [obligation('Музыка', date(2026, 10, 4), 169)],
            OCTOBER,
        )
        self.assertEqual(result['covered_by_previous'], [])
        self.assertEqual(directions(result['allocations'][0])[0], ('Музыка', 169.0, 'overdue'))

    def test_overdue_from_an_earlier_month_is_paid_first(self):
        result = recommend_income_allocation(
            [income(1, date(2026, 10, 5), 1000)],
            [obligation('Связь', date(2026, 10, 6), 400), obligation('Просрочка', date(2026, 9, 5), 800, 'debt')],
            OCTOBER,
        )
        salary = result['allocations'][0]
        self.assertEqual(directions(salary), [('Просрочка', 800.0, 'overdue'), ('Связь', 200.0, 'due')])
        self.assertEqual([(item['label'], item['amount']) for item in salary['shortfalls']], [('Связь', 200.0)])

    def test_goals_and_extra_follow_obligations(self):
        result = recommend_income_allocation(
            [income(1, date(2026, 10, 5), 1500), income(2, date(2026, 10, 20), 5000, 'advance')],
            [
                obligation('Связь', date(2026, 10, 8), 1000),
                pool('Финансовая подушка', 2000),
                pool('Досрочно: Кредит', 1000, kind='extra'),
            ],
            OCTOBER,
        )
        incomes = by_date(result)
        self.assertEqual(directions(incomes[date(2026, 10, 5)]), [
            ('Связь', 1000.0, 'due'), ('Финансовая подушка', 500.0, 'pool'),
        ])
        late = incomes[date(2026, 10, 20)]
        self.assertEqual(directions(late), [
            ('Финансовая подушка', 1500.0, 'pool'),
            ('Досрочно: Кредит', 1000.0, 'pool'),
            ('Повседневные расходы', 2500.0, 'living'),
        ])
        self.assertIn('ранее распределено', late['allocations'][0]['detail'])

    def test_monthly_living_and_deficit_rows_are_not_destinations(self):
        result = recommend_income_allocation(
            [income(1, date(2026, 10, 5), 1000)],
            [
                {'kind': 'living', 'icon': 'x', 'label': 'Повседневные расходы', 'detail': 'месяц', 'amount': 900},
                {'kind': 'deficit', 'icon': 'x', 'label': 'Непокрытый дефицит', 'detail': 'месяц', 'amount': 50},
                obligation('Пусто', date(2026, 10, 6), 0),
            ],
            OCTOBER,
        )
        self.assertEqual(directions(result['allocations'][0]), [('Повседневные расходы', 1000.0, 'living')])

    def test_obligation_without_date_is_paid_with_the_first_dates(self):
        result = recommend_income_allocation(
            [income(1, date(2026, 10, 5), 1000)],
            [obligation('Без даты', None, 400)],
            OCTOBER,
        )
        self.assertEqual(directions(result['allocations'][0])[0], ('Без даты', 400.0, 'due'))

    def test_next_month_dates_take_free_money_but_are_not_warned_here(self):
        result = recommend_income_allocation(
            [income(1, date(2026, 10, 20), 1000, 'advance')],
            [],
            OCTOBER,
            history_incomes=[history(date(2026, 10, 5))],
            next_month_obligations=[
                obligation('Помощь маме', date(2026, 11, 1), 800, 'support'),
                obligation('Музыка', date(2026, 11, 3), 500),
            ],
        )
        advance = result['allocations'][0]
        self.assertEqual(advance['next_income_date'], date(2026, 11, 5))
        self.assertEqual(directions(advance), [('Помощь маме', 800.0, 'due'), ('Музыка', 200.0, 'due')])
        self.assertEqual(result['shortfalls'], [])
        self.assertEqual(result['awaiting'], [])

    def test_month_without_incomes_is_empty(self):
        result = recommend_income_allocation([], [obligation('Связь', date(2026, 10, 8), 500)], OCTOBER)
        self.assertEqual(result, {
            'allocations': [], 'shortfalls': [], 'awaiting': [], 'covered_by_previous': [],
            'reserve_shortfall': 0.0,
        })

    def test_same_day_incomes_split_the_dates(self):
        result = recommend_income_allocation(
            [income(1, date(2026, 10, 5), 300, source='А'), income(2, date(2026, 10, 5), 700, source='Б')],
            [obligation('Аренда', date(2026, 10, 10), 800)],
            OCTOBER,
        )
        rows = sorted(result['allocations'], key=lambda item: item['id'])
        self.assertEqual([row['amount'] for row in rows[0]['allocations']], [300.0])
        self.assertEqual([row['amount'] for row in rows[1]['allocations']], [500.0, 200.0])


class WindowDailyLimitTestCase(unittest.TestCase):
    def test_daily_limit_until_the_next_income(self):
        result = recommend_income_allocation(
            [income(1, date(2026, 10, 5), 20000), income(2, date(2026, 10, 20), 8000, 'advance')],
            [obligation('Связь', date(2026, 10, 8), 2000)],
            OCTOBER,
        )
        limit = window_daily_limit(result['allocations'], date(2026, 10, 10), lambda start, today: 3000, NOVEMBER)
        self.assertEqual(limit['start'], date(2026, 10, 5))
        self.assertEqual(limit['end'], date(2026, 10, 20))
        self.assertFalse(limit['end_is_estimated'])
        self.assertEqual(limit['living'], 18000.0)
        self.assertEqual(limit['available'], 15000.0)
        self.assertEqual(limit['days_left'], 10)
        self.assertEqual(limit['daily'], 1500.0)

    def test_single_salary_expects_the_next_one_a_month_later(self):
        result = recommend_income_allocation([income(1, date(2026, 10, 5), 1000)], [], OCTOBER)
        limit = window_daily_limit(result['allocations'], date(2026, 10, 21), lambda start, today: 0, NOVEMBER)
        self.assertEqual(limit['end'], date(2026, 11, 5))
        self.assertTrue(limit['end_is_estimated'])
        self.assertEqual(limit['days_left'], 15)

    def test_without_main_payments_counts_to_month_end_and_overspending_gives_zero(self):
        result = recommend_income_allocation(
            [income(1, date(2026, 10, 5), 1000, 'bonus', mode='join')], [], OCTOBER,
        )
        limit = window_daily_limit(result['allocations'], date(2026, 10, 21), lambda start, today: 5000, NOVEMBER)
        self.assertIsNone(limit['end'])
        self.assertEqual(limit['days_left'], 11)
        self.assertEqual(limit['available'], -4000.0)
        self.assertEqual(limit['daily'], 0.0)

    def test_no_limit_before_the_first_income(self):
        result = recommend_income_allocation([income(1, date(2026, 10, 5), 1000)], [], OCTOBER)
        self.assertIsNone(window_daily_limit(result['allocations'], date(2026, 10, 2), lambda s, t: 0, NOVEMBER))
        self.assertIsNone(window_daily_limit([], date(2026, 10, 2), lambda s, t: 0, NOVEMBER))


if __name__ == '__main__':
    unittest.main()
