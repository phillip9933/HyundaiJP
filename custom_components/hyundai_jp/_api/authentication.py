"""Japan Bluelink HTTP envelopes, OAuth and device registration."""

import base64
import datetime as dt
import re
import time
from typing import Any, ClassVar
from urllib.parse import parse_qs, urljoin, urlparse
import uuid

import requests

from .exceptions import (
    APIError,
    AuthenticationError,
    BluelinkError,
    ConsentRequiredError,
    DuplicateRequestError,
    InvalidAPIResponseError,
    NoDataFound,
    RateLimitingError,
    RequestTimeoutError,
    ServiceTemporaryUnavailable,
)
from .http import BluelinkSession
from .Token import Token


class JapanAuthClient:
    """Own one HTTP session and the Japan account authentication flow."""

    API_URL = "https://ccs-apigw.hyundaijapan.com:8080"
    SERVICE_ID = "e084c0e4-fcfe-47e3-99c9-36eb93593737"
    APP_ID = "8be122e0-b0d9-40b5-a46e-67b25194ba88"
    REDIRECT_URI = API_URL + "/api/v1/user/oauth2/redirect"

    # CFB128 keystream for the fixed UUID prefix and a ten-digit epoch.
    # Derived offline from Japan Android 1.0.17; no app binary is distributed.
    CFB = base64.b64decode(
        "yGeH2vkxepxXpBulEUnI04QXO3CM9oT4E20p+AB8lqHuRZmVEfdQlJmzBX5z5pk="
    )

    BASIC_AUTHORIZATION = "Basic ZTA4NGMwZTQtZmNmZS00N2UzLTk5YzktMzZlYjkzNTkzNzM3OjFZcWV4dng3TWxiS3c2YUFvYk1FbDJqQVFvQTBtUVhyV1E1SWpIT083TzRZRHRkRw=="

    _KNOWN_ERRORS: ClassVar[dict[str, tuple[type[BluelinkError], str]]] = {
        "4002": (APIError, "invalid request"),
        "4003": (APIError, "invalid value"),
        "4004": (DuplicateRequestError, "duplicate request"),
        "4006": (APIError, "account locked"),
        "4016": (APIError, "account migration required"),
        "4081": (RequestTimeoutError, "request expired"),
        "5031": (ServiceTemporaryUnavailable, "remote control unavailable"),
        "5091": (RateLimitingError, "request limit reached"),
        "5911": (NoDataFound, "no data found"),
        "5921": (NoDataFound, "no data found"),
        "7501": (AuthenticationError, "authentication expired"),
        "4292": (RateLimitingError, "surround-view capture limit reached"),
        "7779": (ServiceTemporaryUnavailable, "surround-view unavailable"),
        "7780": (APIError, "surround-view camera error"),
        "7781": (APIError, "surround-view blocked by emergency lights"),
        "7782": (APIError, "surround-view blocked by low battery"),
        "5016": (APIError, "vehicle has not uploaded data"),
        "4101": (APIError, "vehicle is not registered"),
        "4140": (APIError, "vehicle profile is not owned by this account"),
        "4008": (APIError, "vehicle system is updating"),
        "4901": (APIError, "vehicle is immobilized"),
        "5311": (APIError, "stolen-vehicle remote control unavailable"),
        "5152": (APIError, "no GPS data found"),
        "4011": (APIError, "verification number is incorrect"),
        "4291": (APIError, "authentication already processed"),
    }

    def __init__(self) -> None:
        self.session = BluelinkSession()

    def _stamp(self) -> str:
        epoch = str(int(time.time()))
        if len(epoch) != 10:
            raise APIError("Japan stamp requires a supported ten-digit epoch")
        return self._stamp_for_epoch(int(epoch))

    def _headers(self, token: Token | None = None) -> dict[str, str]:
        headers = {
            "ccsp-service-id": self.SERVICE_ID,
            "ccsp-application-id": self.APP_ID,
            "offset": "9",
            "Stamp": self._stamp(),
        }
        if token is not None:
            headers["Authorization"] = token.access_token or ""
            headers["ccsp-device-id"] = token.device_id or ""
        return headers

    @classmethod
    def _diagnostic(cls, http_status: int | None, result: dict[str, Any]) -> str:
        details = []
        if type(http_status) is int and 100 <= http_status <= 599:
            details.append(f"HTTP {http_status}")
        code = str(
            result.get("resCode", result.get("resultCode", result.get("errCode", "")))
        )
        known = cls._KNOWN_ERRORS.get(code)
        if known is not None:
            details.append(f"code {code}: {known[1]}")
        return " (" + ", ".join(details) + ")" if details else ""

    def _request(
        self, method: str, path: str, *, json_response: bool = True, **kwargs: Any
    ) -> Any:
        """Expose HTTP status and allowlisted labels, never response text or URLs."""
        kwargs.setdefault("allow_redirects", False)
        try:
            response = self.session.request(method, self.API_URL + path, **kwargs)
        except requests.exceptions.Timeout, RequestTimeoutError:
            raise RequestTimeoutError("Japan API request timed out") from None
        except requests.exceptions.RequestException:
            raise APIError("Japan API connection failed") from None
        status = response.status_code
        if not json_response and (200 <= status < 300 or 300 <= status < 400):
            return response
        try:
            result = response.json()
        except ValueError:
            if 200 <= status < 300:
                raise InvalidAPIResponseError(
                    f"Japan API returned invalid JSON (HTTP {status})"
                ) from None
            result = {}
        if not isinstance(result, dict):
            if 200 <= status < 300:
                raise InvalidAPIResponseError(
                    f"Japan API returned an invalid object (HTTP {status})"
                )
            result = {}
        diagnostic = self._diagnostic(status, result)
        if status in (401, 403):
            rejection: BluelinkError = AuthenticationError(
                "Japan authentication was rejected" + diagnostic
            )
            rejection.request_rejected = True
            raise rejection
        if status == 429:
            rejection = RateLimitingError("Japan API rate limit reached" + diagnostic)
            rejection.request_rejected = True
            raise rejection
        if status >= 500:
            raise ServiceTemporaryUnavailable("Japan API is unavailable" + diagnostic)
        self._check_errors(result, http_status=status, path=path)
        if not 200 <= status < 300:
            error = APIError("Japan API request was rejected" + diagnostic)
            error.request_rejected = 400 <= status < 500 and status != 408
            raise error
        return result

    @classmethod
    def _check_errors(
        cls,
        result: dict[str, Any],
        *,
        http_status: int | None = None,
        path: str | None = None,
    ) -> None:
        code = str(
            result.get("resCode", result.get("resultCode", result.get("errCode", "")))
        )
        diagnostic = cls._diagnostic(http_status, result)
        if path == "/api/v1/user/signin" and code in ("4003", "4006", "4016"):
            raise AuthenticationError("Japan sign-in was rejected" + diagnostic)
        if path == "/api/v1/user/pin" and code == "4003":
            raise AuthenticationError("Japan PIN was rejected" + diagnostic)
        if result.get("error") in ("invalid_grant", "invalid_token"):
            raise AuthenticationError("Japan token grant was rejected" + diagnostic)
        known = cls._KNOWN_ERRORS.get(code)
        if known is not None:
            error = known[0]("Japan API rejected the request" + diagnostic)
            error.request_rejected = http_status != 408
            raise error
        failure_marker = result.get("retCode", result.get("resultType"))
        if (
            (isinstance(failure_marker, str) and failure_marker.upper() == "F")
            or (code and code != "0000")
            or result.get("error")
        ):
            error = APIError("Japan API rejected the request" + diagnostic)
            error.request_rejected = http_status != 408
            raise error

    @staticmethod
    def _payload(result: dict[str, Any]) -> dict[str, Any]:
        payload = result.get("resMsg", result.get("responseEntity"))
        if not isinstance(payload, dict):
            raise InvalidAPIResponseError("Japan API response payload is missing")
        return payload

    def _exchange_token(
        self,
        grant: dict[str, Any],
        username: str,
        device_id: str | None,
        pin: str | None,
    ) -> Token:
        headers = self._headers()
        headers["Authorization"] = self.BASIC_AUTHORIZATION
        result = self._request(
            "POST",
            "/api/v1/user/oauth2/token",
            headers=headers,
            data={
                "client_id": self.SERVICE_ID,
                "redirect_uri": self.REDIRECT_URI,
                **grant,
            },
        )
        try:
            access = result["access_token"]
            token_type = result["token_type"]
            expiry = result["expires_in"]
            if isinstance(expiry, bool) or not re.fullmatch(r"[0-9]+", str(expiry)):
                raise ValueError
            lifetime = int(expiry)
            if not isinstance(access, str) or not access or lifetime <= 0:
                raise ValueError
            if token_type != "Bearer":
                raise ValueError
            refresh = result.get("refresh_token", grant.get("refresh_token"))
            if not isinstance(refresh, str) or not refresh:
                raise ValueError
            valid_until = dt.datetime.now(dt.UTC) + dt.timedelta(seconds=lifetime)
        except KeyError, TypeError, ValueError, OverflowError:
            raise InvalidAPIResponseError(
                "Japan token response is incomplete"
            ) from None
        return Token(
            username=username,
            access_token=f"{token_type} {access}",
            refresh_token=refresh,
            device_id=device_id,
            pin=pin,
            valid_until=valid_until,
        )

    def login(self, username: str, password: str, pin: str | None = None) -> Token:
        """Establish cookies, sign in, exchange the code, and register a device."""
        response = self._request(
            "GET",
            "/api/v1/user/oauth2/authorize",
            json_response=False,
            allow_redirects=False,
            params={
                "response_type": "code",
                "client_id": self.SERVICE_ID,
                "redirect_uri": self.REDIRECT_URI,
                "lang": "ja",
            },
        )
        for _ in range(5):
            if not 300 <= response.status_code < 400:
                break
            target = urljoin(response.url, response.headers.get("Location", ""))
            parsed = urlparse(target)
            expected = urlparse(self.API_URL)
            if (parsed.scheme, parsed.netloc) != (expected.scheme, expected.netloc):
                raise AuthenticationError(
                    "Japan login redirected to an unexpected host"
                )
            response = self._request(
                "GET",
                parsed.path + ("?" + parsed.query if parsed.query else ""),
                json_response=False,
                allow_redirects=False,
            )
        else:
            raise AuthenticationError("Japan login exceeded its redirect limit")
        self._request(
            "POST", "/api/v1/user/language", json_response=False, json={"lang": "ja"}
        )
        result = self._request(
            "POST",
            "/api/v1/user/signin",
            json={"email": username, "password": password, "mobileNum": ""},
        )
        redirect = result.get("redirectUrl")
        if not isinstance(redirect, str):
            raise ConsentRequiredError(
                "Complete Japan account setup in the official app"
            )
        parsed = urlparse(redirect)
        expected = urlparse(self.REDIRECT_URI)
        code = parse_qs(parsed.query).get("code", [])
        if (
            (parsed.scheme, parsed.netloc, parsed.path)
            != (
                expected.scheme,
                expected.netloc,
                expected.path,
            )
            or len(code) != 1
            or not code[0]
        ):
            raise AuthenticationError(
                "Japan login returned no valid authorization code"
            )
        token = self._exchange_token(
            {"grant_type": "authorization_code", "code": code[0]},
            username,
            None,
            pin,
        )
        headers = self._headers()
        # The app uses blank placeholders; requests rejects whitespace header
        # values. This fresh session has no default authorization/device header.
        headers.update({"Authorization": "", "ccsp-device-id": ""})
        registration_uuid = str(uuid.uuid4())
        result = self._request(
            "POST",
            "/api/v1/spa/notifications/register",
            headers=headers,
            json={
                "uuid": registration_uuid,
                "pushRegId": uuid.uuid4().hex,
                "pushType": "GCM",
            },
        )
        token.device_id = self._payload(result).get("deviceId")
        if not isinstance(token.device_id, str) or not token.device_id:
            raise InvalidAPIResponseError("Japan device registration was incomplete")
        token.mqtt_registration_uuid = registration_uuid
        return token

    def refresh_access_token(self, token: Token) -> Token:
        """Use the refresh grant; preserve the registered device identity."""
        if not token.refresh_token:
            raise AuthenticationError("Japan refresh token is missing; sign in again")
        refreshed = self._exchange_token(
            {"grant_type": "refresh_token", "refresh_token": token.refresh_token},
            token.username,
            token.device_id,
            token.pin,
        )
        refreshed.mqtt_registration_uuid = token.mqtt_registration_uuid
        refreshed.mqtt_client_id = token.mqtt_client_id
        return refreshed

    def _stamp_for_epoch(self, epoch: int) -> str:
        raw_data = f"{self.APP_ID}:{epoch}".encode()
        result = bytes(b1 ^ b2 for b1, b2 in zip(self.CFB, raw_data))
        return base64.b64encode(result).decode("utf-8")
