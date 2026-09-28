from django.conf import settings
from django.middleware import csp


def client_ip(request) -> str | None:
    """The visitor's address for django-axes.

    Behind Caddy every request comes from the proxy container, so ``REMOTE_ADDR`` would lock out
    everyone at once. Caddy replaces a client-sent ``X-Forwarded-For`` with the real address, and
    the app port is not published, so the last entry is trustworthy.
    """
    if settings.BEHIND_PROXY:
        forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
        last = forwarded.rsplit(",", 1)[-1].strip()
        if last:
            return last
    return request.META.get("REMOTE_ADDR")


def is_admin_path(path: str) -> bool:
    return path.startswith(f"/{settings.ADMIN_URL}")


class ContentSecurityPolicyMiddleware(csp.ContentSecurityPolicyMiddleware):
    """Django's CSP middleware with the wider ``ADMIN_CSP`` under ``ADMIN_URL``.

    A view's own ``csp_override`` still wins: the policy is only chosen when none was set.
    """

    def process_response(self, request, response):
        if is_admin_path(request.path) and not hasattr(response, "_csp_config"):
            response._csp_config = settings.ADMIN_CSP
        return super().process_response(request, response)
