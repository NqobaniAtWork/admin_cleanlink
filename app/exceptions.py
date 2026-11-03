class CleanLinkAdminError(Exception):
    """Base class for expected admin-domain failures."""


class AuthenticationError(CleanLinkAdminError):
    pass


class AuthenticationUnavailableError(CleanLinkAdminError):
    pass


class AuthenticationRateLimitError(CleanLinkAdminError):
    pass


class PermissionDeniedError(CleanLinkAdminError):
    pass


class VerificationRequiredError(CleanLinkAdminError):
    pass


class SubscriptionExpiredError(CleanLinkAdminError):
    pass


class CleanerSuspendedError(CleanLinkAdminError):
    pass


class AdminManagementError(CleanLinkAdminError):
    pass
