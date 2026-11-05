"""Pooled Bluelink HTTP session with bounded requests and no command replay."""

from typing import Any

import requests


class BluelinkSession(requests.Session):
    """Shared HTTP session with default timeout and connection pooling.

    Japan requests use bounded connect/read timeouts. Vehicle commands are
    never automatically replayed because a timeout can have an unknown outcome.
    """

    HTTP_CONNECT_TIMEOUT = 10
    HTTP_READ_TIMEOUT = 30

    def request(
        self, method: str | bytes, url: str | bytes, *args: Any, **kwargs: Any
    ) -> requests.Response:
        kwargs.setdefault(
            "timeout", (self.HTTP_CONNECT_TIMEOUT, self.HTTP_READ_TIMEOUT)
        )
        try:
            return super().request(
                method.decode("ascii") if isinstance(method, bytes) else method,
                url,
                *args,
                **kwargs,
            )
        except requests.exceptions.Timeout as exc:
            from .exceptions import RequestTimeoutError

            raise RequestTimeoutError(str(exc)) from exc
