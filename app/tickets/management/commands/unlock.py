"""Lift a sign-in pause after too many wrong passwords:  manage.py unlock <user name>   or   manage.py unlock --all"""
from django.core.management.base import BaseCommand, CommandError

from tickets import signin
from tickets.models import SignInLock


class Command(BaseCommand):
    help = "Lift the sign-in pause for a user name (after too many wrong passwords), or every pause with --all."

    def add_arguments(self, parser):
        parser.add_argument("username", nargs="?")
        parser.add_argument("--all", action="store_true", help="Lift every pause, for user names and computers.")

    def handle(self, *args, **opts):
        if opts["all"]:
            n = SignInLock.objects.all().delete()[0]
        elif opts["username"]:
            n = signin.unlock(username=opts["username"])
        else:
            raise CommandError("Give a user name, or --all.")
        self.stdout.write("Sign-in unlocked." if n else "Nothing was locked.")
