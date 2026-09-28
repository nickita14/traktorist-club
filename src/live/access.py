from functools import wraps

from django.conf import settings
from django.contrib.auth.views import redirect_to_login
from django.http import HttpResponse, HttpResponseForbidden
from django.urls import reverse
from django.views.decorators.csp import csp_override

ORGANIZER_GROUP = "Organizer"


def is_organizer(user) -> bool:
    return user.is_superuser or user.groups.filter(name=ORGANIZER_GROUP).exists()


def signed_in(request) -> bool:
    user = request.user
    if not user.is_authenticated:
        return False
    # With 2FA on, the admin requires a verified session; so do the live screens, or they would
    # be a way around it.
    return not settings.ADMIN_REQUIRE_2FA or user.is_verified()


def organizer_required(view):
    """Organizers and superusers only; everyone else goes to the admin login or gets 403.

    The live screens live under ADMIN_URL but use the strict public CSP, not ADMIN_CSP.
    """

    @csp_override(settings.SECURE_CSP)
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        if not signed_in(request):
            login = reverse("admin:login")
            if request.headers.get("HX-Request"):
                # htmx would follow a 302 and swap the login page into the board.
                response = HttpResponse(status=204)
                response["HX-Redirect"] = f"{login}?next={request.path}"
                return response
            return redirect_to_login(request.get_full_path(), login)
        if not is_organizer(request.user):
            return HttpResponseForbidden("Только для организаторов клуба.")
        return view(request, *args, **kwargs)

    return wrapper
