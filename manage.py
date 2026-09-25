#!/usr/bin/env python
"""Django entry point for development and tests: python manage.py test qqbot"""

import os
import sys

if __name__ == "__main__":
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "testauth.settings")
    from django.core.management import execute_from_command_line

    execute_from_command_line(sys.argv)
