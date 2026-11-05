"""exceptions.py"""


class BluelinkError(Exception):
    """
    Base exception for Japan Bluelink failures.
    """


class AuthenticationError(BluelinkError):
    """
    Raised upon receipt of an authentication error.
    """


class APIError(BluelinkError):
    """
    Generic API error
    """


class RateLimitingError(APIError):
    """
    Raised when we get rate limited by the server
    """


class NoDataFound(APIError):
    """
    Raised when a requested status, capture or optional data is not available.
    """


class ServiceTemporaryUnavailable(APIError):
    """
    Raised when Service Temporary Unavailable
    """


class DuplicateRequestError(APIError):
    """
    Raised when (supposedly) a previous request is already queued server-side
    and the server temporarily rejects requests.
    """


class SafetyAcknowledgmentError(APIError):
    """
    Raised when a safety-critical command requires explicit user acknowledgment
    before it can be executed (e.g. SVM/Find My Car capture).
    """


class UnsupportedControlError(APIError):
    """
    Raised when the vehicle does not support the requested control action.
    API returns resCode 4005 for unsupported actions (e.g. stop_climate on HEV).
    """


class RequestTimeoutError(APIError):
    """
    Raised when (supposedly) the server fails to establish a connection with the car.
    """


class InvalidAPIResponseError(APIError):
    """
    Raised upon receipt of an invalid API response.
    """


class ConsentRequiredError(AuthenticationError):
    """
    Raised when the OAuth signin succeeds but the user must accept
    a consent/authorization page before tokens can be issued.
    This typically means the user needs to log in via a browser once
    to accept terms, then use the refresh token going forward.
    """


class PartialCommandError(APIError):
    """Some independent schedule submissions were accepted before another failed."""

    def __init__(
        self,
        accepted_scopes: tuple[str, ...],
        failed_scope: str,
        *,
        outcome_unknown: bool,
    ) -> None:
        self.accepted_scopes = accepted_scopes
        self.failed_scope = failed_scope
        self.outcome_unknown = outcome_unknown
        state = "submission outcome is unknown" if outcome_unknown else "request failed"
        super().__init__(
            f"Schedule partially submitted: {', '.join(accepted_scopes)} accepted; "
            f"{failed_scope} {state}. Accepted does not confirm vehicle execution. "
            "Check the Bluelink settings before retrying."
        )
