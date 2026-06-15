#!/usr/bin/env python
import os
import sys


def main():
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "snde_backend.settings")
    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:
        raise ImportError(
            "Django introuvable. L'image Docker n'a peut-être pas installé requirements.txt."
        ) from exc
    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
