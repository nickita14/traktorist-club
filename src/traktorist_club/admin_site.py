from django import forms
from django.conf import settings
from django.contrib.auth.signals import user_login_failed
from django.core.exceptions import ValidationError
from django_otp import devices_for_user
from django_otp.forms import OTPAuthenticationFormMixin
from unfold.forms import AuthenticationForm
from unfold.sites import UnfoldAdminSite
from unfold.widgets import BASE_INPUT_CLASSES


class LoginForm(OTPAuthenticationFormMixin, AuthenticationForm):
    """Unfold's login form plus a TOTP code when ``ADMIN_REQUIRE_2FA`` is on.

    Each user has one TOTP device (``manage.py totp_enroll``), so there is no device picker: the
    code is checked against the user's confirmed device. A wrong code counts as a failed login for
    django-axes, so guessing codes runs into the same lockout as guessing passwords.
    """

    otp_token = forms.CharField(
        label="Код из приложения",
        required=False,
        widget=forms.TextInput(
            attrs={
                "autocomplete": "one-time-code",
                "inputmode": "numeric",
                "class": " ".join(BASE_INPUT_CLASSES),
            }
        ),
    )

    otp_error_messages = dict(
        OTPAuthenticationFormMixin.otp_error_messages,
        token_required="Введите код из приложения.",
        invalid_token="Неверный код. Проверьте его и попробуйте ещё раз.",
        # After each wrong code django-otp refuses codes for a while (1 s, 2 s, 4 s, ...).
        n_failed_attempts=(
            "Слишком много неверных кодов подряд. Подождите немного и попробуйте снова."
        ),
        verification_not_allowed="Проверка кода временно недоступна. Попробуйте позже.",
    )

    def __init__(self, request=None, *args, **kwargs):
        super().__init__(request, *args, **kwargs)
        if not settings.ADMIN_REQUIRE_2FA:
            del self.fields["otp_token"]

    def clean(self):
        super().clean()
        if settings.ADMIN_REQUIRE_2FA:
            user = self.get_user()
            device = next(devices_for_user(user, confirmed=True), None)
            if device is None:
                raise ValidationError(
                    "Для этой учётной записи не настроен второй фактор входа.",
                    code="no_otp_device",
                )
            self.cleaned_data["otp_device"] = device.persistent_id
            try:
                self.clean_otp(user)
            except ValidationError:
                user_login_failed.send(
                    sender=__name__,
                    credentials={"username": user.get_username()},
                    request=self.request,
                )
                raise
        return self.cleaned_data

    def _update_form(self, user):
        # django-otp re-renders the typed password into the page after a wrong code, and fills a
        # device picker this form does not have. Keep neither.
        pass


class ClubAdminSite(UnfoldAdminSite):
    """Unfold's admin site that, with ``ADMIN_REQUIRE_2FA``, admits only OTP-verified users."""

    login_form = LoginForm

    def has_permission(self, request):
        if not super().has_permission(request):
            return False
        return not settings.ADMIN_REQUIRE_2FA or request.user.is_verified()
