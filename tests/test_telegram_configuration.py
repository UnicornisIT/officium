import unittest

from app.services.telegram_configuration_service import receiver_configuration


class TelegramReceiverConfigurationTests(unittest.TestCase):
    def config(self, **values):
        return dict(
            TELEGRAM_BOT_TOKEN='test-token',
            TELEGRAM_RECEIVE_MODE='webhook',
            TELEGRAM_WEBHOOK_SECRET='test-secret',
            OFFICIUM_PUBLIC_URL='https://example.test',
            **values
        )

    def test_webhook_requires_every_parameter_used_by_setup(self):
        original = self.config()
        for field, value, error in (
            ('TELEGRAM_BOT_TOKEN', '', 'missing_token'),
            ('TELEGRAM_WEBHOOK_SECRET', '', 'missing_webhook_secret'),
            ('OFFICIUM_PUBLIC_URL', '', 'invalid_public_url'),
            ('OFFICIUM_PUBLIC_URL', 'http://example.test', 'invalid_public_url'),
            ('OFFICIUM_PUBLIC_URL', 'https://example.test:bad', 'invalid_public_url'),
            ('OFFICIUM_PUBLIC_URL', 'https://[invalid', 'invalid_public_url'),
            (
                'OFFICIUM_PUBLIC_URL',
                'https://user:password@example.test',
                'invalid_public_url',
            ),
            (
                'OFFICIUM_PUBLIC_URL',
                'https://example.test?key=secret',
                'invalid_public_url',
            ),
        ):
            with self.subTest(field=field, value=value):
                result = receiver_configuration({**original, field: value})
                self.assertFalse(result['configured'])
                self.assertEqual(result['error'], error)
                self.assertNotIn('password', str(result))
        self.assertTrue(receiver_configuration(original)['configured'])

    def test_polling_only_requires_token_and_never_exposes_invalid_mode(self):
        result = receiver_configuration(
            {'TELEGRAM_BOT_TOKEN': 'test-token', 'TELEGRAM_RECEIVE_MODE': 'polling'}
        )
        self.assertTrue(result['configured'])
        self.assertIsNone(result['error'])
        result = receiver_configuration({'TELEGRAM_RECEIVE_MODE': 'private-value'})
        self.assertFalse(result['configured'])
        self.assertEqual(result['receive_mode'], 'invalid')
        self.assertNotIn('private-value', str(result))
