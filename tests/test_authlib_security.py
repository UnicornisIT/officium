"""The pinned OAuth dependency must reject unsigned JWS JSON objects."""
import base64
import unittest

from authlib.jose import JsonWebSignature
from authlib.jose.errors import JoseError


class AuthlibSecurityTestCase(unittest.TestCase):
    def test_empty_signature_array_is_never_verified(self):
        payload = base64.urlsafe_b64encode(b'{"sub":"fictional-user"}').rstrip(b'=').decode()
        unsigned = {'payload': payload, 'signatures': []}
        for key in (None, b'fictional-test-key'):
            with self.subTest(has_key=key is not None), self.assertRaises(JoseError):
                JsonWebSignature().deserialize_json(unsigned, key=key)
