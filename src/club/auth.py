"""The organizers' login on the public site ("Проходная"), outside ADMIN_URL.

It is the admin login in the ledger style, not a second, weaker door: the same form class
(``traktorist_club.admin_site.LoginForm``: the authentication backends, so django-axes counts
failures and locks out the same username and address, plus the TOTP code with
``ADMIN_REQUIRE_2FA``), CSRF, POST only. Only organizers get in, and the page never contains
ADMIN_URL.
"""

from urllib.parse import urlsplit, urlunsplit

from django.contrib.auth import logout
from django.contrib.auth import views as auth_views
from django.contrib.auth.forms import AuthenticationForm
from django.contrib.auth.signals import user_login_failed
from django.shortcuts import redirect
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from live.access import is_organizer, signed_in
from traktorist_club.admin_site import LoginForm
from traktorist_club.security import is_admin_path

# One message for a wrong password, an unknown user and a non-organizer, so the page never tells
# whether a password was right.
REFUSED = "Вход только для организаторов. Проверьте имя пользователя и пароль."


class OrganizerLoginForm(LoginForm):
    error_messages = {**LoginForm.error_messages, "invalid_login": REFUSED}

    def confirm_login_allowed(self, user):
        # Django's own check (active users) instead of the admin's is_staff one: organizers need
        # the live screens, not necessarily the admin.
        AuthenticationForm.confirm_login_allowed(self, user)
        if not is_organizer(user):
            # A right password for a non-organizer counts as a failure too, so neither the message
            # nor the lockout tells it apart from a wrong one.
            user_login_failed.send(
                sender=__name__,
                credentials={"username": user.get_username()},
                request=self.request,
            )
            raise self.get_invalid_login_error()


def safe_next(request, url: str | None) -> str | None:
    """``url`` as a local path, or None when it is off-site, under ADMIN_URL or the login pages."""
    if not url:
        return None
    if not url_has_allowed_host_and_scheme(
        url, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return None
    parts = urlsplit(url)
    local = urlunsplit(("", "", parts.path or "/", parts.query, ""))
    # "http://host//evil.example/" keeps a protocol-relative path: check the result again.
    if not url_has_allowed_host_and_scheme(local, allowed_hosts={request.get_host()}):
        return None
    if is_admin_path(parts.path) or parts.path in (reverse("login"), reverse("logout")):
        return None
    return local


class LoginView(auth_views.LoginView):
    form_class = OrganizerLoginForm
    template_name = "club/login.html"

    def get(self, request, *args, **kwargs):
        if signed_in(request) and is_organizer(request.user):
            return redirect(self.get_success_url())
        return super().get(request, *args, **kwargs)

    def get_redirect_url(self):
        return safe_next(self.request, self.request.POST.get("next", self.request.GET.get("next")))

    def get_default_redirect_url(self):
        return reverse("home")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # Where to go after the login: a safe "next", else the page the visitor came from.
        context["next"] = (
            self.get_redirect_url()
            or safe_next(self.request, self.request.headers.get("Referer"))
            or reverse("home")
        )
        context["site_indexing"] = False
        return context

    def render_to_response(self, context, **response_kwargs):
        response = super().render_to_response(context, **response_kwargs)
        response["X-Robots-Tag"] = "noindex, nofollow"
        return response


@require_POST
def logout_view(request):
    logout(request)
    return redirect(safe_next(request, request.POST.get("next")) or reverse("home"))
