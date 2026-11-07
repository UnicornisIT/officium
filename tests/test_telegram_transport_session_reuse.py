from unittest.mock import Mock, patch

import requests
from requests.adapters import HTTPAdapter

from app.services import telegram_transport as transport


def test_api_request_reuses_session():
    session_type = requests.Session
    response = Mock(status_code=200)
    response.json.return_value = {"ok": True, "result": {"message_id": 42}}

    transport.reset_session()
    try:
        with (
            patch.object(
                session_type, "post", autospec=True, return_value=response
            ) as post,
            patch.object(
                transport.requests, "Session", wraps=session_type
            ) as create_session,
        ):
            for _ in range(5):
                result, _ = transport.api_request(
                    "sendMessage", {}, bot_token="test-token"
                )
                assert result.accepted

            create_session.assert_called_once_with()
            assert post.call_count == 5
            session = post.call_args_list[0].args[0]
            assert all(call.args[0] is session for call in post.call_args_list)

            adapter = session.get_adapter("https://api.telegram.org")
            assert isinstance(adapter, HTTPAdapter)
            assert adapter.poolmanager.pools._maxsize == 2
            assert adapter.poolmanager.connection_pool_kw["maxsize"] == 10
            assert adapter.max_retries.total == 0

            transport.reset_session()
            transport.api_request("sendMessage", {}, bot_token="rotated-test-token")
            assert create_session.call_count == 2
            assert post.call_args.args[0] is not session
    finally:
        transport.reset_session()
