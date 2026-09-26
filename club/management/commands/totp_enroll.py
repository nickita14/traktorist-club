import io

import qrcode
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django_otp.plugins.otp_totp.models import TOTPDevice

MAX_ATTEMPTS = 3


class Command(BaseCommand):
    help = (
        "Set up the admin's second login factor for a user: prints a QR code for an authenticator "
        "app, then asks for one code from the app. The new device replaces the user's old ones "
        "only after that code checks out, so a mis-scanned QR code never locks anyone out."
    )

    def add_arguments(self, parser):
        parser.add_argument("username")

    def handle(self, *args, username, **options):
        user_model = get_user_model()
        try:
            user = user_model.objects.get(**{user_model.USERNAME_FIELD: username})
        except user_model.DoesNotExist as exc:
            raise CommandError(f"No user {username!r}.") from exc

        device = TOTPDevice.objects.create(user=user, name="Приложение", confirmed=False)
        try:
            self._show(device)
            self._confirm(device)
        except BaseException:
            device.delete()
            raise

        with transaction.atomic():
            TOTPDevice.objects.filter(user=user).exclude(pk=device.pk).delete()
            device.confirmed = True
            device.save(update_fields=["confirmed"])
        self.stdout.write(self.style.SUCCESS(f"2FA is set up for {username}."))

    def _show(self, device):
        code = qrcode.QRCode(border=2)
        code.add_data(device.config_url)
        picture = io.StringIO()
        code.print_ascii(out=picture, invert=True)
        self.stdout.write("Scan this with an authenticator app (Aegis, Google Authenticator, ...):")
        self.stdout.write(picture.getvalue())
        self.stdout.write(f"Or add it by hand: {device.config_url}")

    def _confirm(self, device):
        for _ in range(MAX_ATTEMPTS):
            token = input("Code from the app: ").strip()
            if device.verify_token(token):
                return
            self.stderr.write("Wrong code.")
        raise CommandError("No valid code: nothing changed, the old device (if any) still works.")
