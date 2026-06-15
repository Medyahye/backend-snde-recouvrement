"""Crée (ou met à jour) le superuser admin par défaut depuis les variables .env."""
import os

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand

User = get_user_model()


class Command(BaseCommand):
    help = (
        "Crée le superuser admin SNDE depuis les variables DJANGO_SUPERUSER_EMAIL "
        "et DJANGO_SUPERUSER_PASSWORD. Idempotent (n'écrase pas un user existant)."
    )

    def handle(self, *args, **options):
        email = os.environ.get("DJANGO_SUPERUSER_EMAIL", "admin@snde.local")
        password = os.environ.get("DJANGO_SUPERUSER_PASSWORD", "admin")
        # On utilise l'email comme username pour rester cohérent avec un futur login email.
        username = email

        user, created = User.objects.get_or_create(
            username=username,
            defaults={
                "email": email,
                "is_staff": True,
                "is_superuser": True,
                "role": User.Role.ADMIN,
            },
        )
        if created:
            user.set_password(password)
            user.save()
            self.stdout.write(self.style.SUCCESS(f"Superuser créé : {username}"))
        else:
            self.stdout.write(
                self.style.WARNING(
                    f"Superuser déjà existant : {username} (mot de passe non modifié)"
                )
            )
