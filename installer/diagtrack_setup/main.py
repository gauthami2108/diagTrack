"""Entry point: picks Windows or Docker, and new install, update, repair, remove or check."""
import argparse
import json
from pathlib import Path

from . import ui


def main(argv):
    p = argparse.ArgumentParser(prog="setup", description="DiagTrack setup")
    p.add_argument("--windows", action="store_true")
    p.add_argument("--docker", action="store_true")
    p.add_argument("--source", default=str(Path(__file__).resolve().parent.parent.parent))
    p.add_argument("--answers", default="", help="JSON file with answers, for an unattended run")
    p.add_argument("action", nargs="?", default="", choices=["", "install", "move", "update", "repair", "remove", "verify"])
    args = p.parse_args(argv)
    if args.answers:
        ui.ANSWERS.update(json.loads(Path(args.answers).read_text()))

    if args.docker:
        from . import docker_mode as mod
        log = Path(__import__("os").environ.get("STATUS_DIR", "/data/status")) / "setup.log"
    else:
        from . import windows as mod
        log = mod.DATA / "setup.log"
    ui.start_log(log)
    ui.banner("DiagTrack setup", "Docker" if args.docker else "Windows: native install")
    try:
        if args.docker:
            code = mod.verify() if args.action == "verify" else mod.install()
            return code
        installed = mod.load_state().get("installed")
        action = args.action
        if not action:
            if installed:
                st = mod.load_state()
                ui.say(f"DiagTrack {st.get('version', '')} is installed here (since {st.get('installed_at', '?')}).")
                action = ui.choose("What would you like to do?", [
                    ("update", "Update to the version in this folder (backs up first, switches back if it fails)"),
                    ("repair", "Repair: check the service, permissions, firewall and safety net"),
                    ("remove", "Remove DiagTrack from this computer"),
                    ("quit", "Nothing, close setup")], key="action")
            else:
                action = ui.choose("What would you like to do?", [
                    ("install", "New install"),
                    ("move", "Move in from another DiagTrack server (needs its move file and passphrase)")], key="action")
        if action == "quit":
            return 0
        if action in ("install", "move"):
            code = mod.install(args.source, "move" if action == "move" else "new")
        elif action == "update":
            code = mod.update(args.source)
        elif action == "repair":
            code = mod.repair(args.source)
        elif action == "remove":
            code = mod.remove()
        else:
            code = 0
        return ui.pause_exit(code)
    except ui.Failed as f:
        ui.show_failure(f, log)
        return ui.pause_exit(2)
    except KeyboardInterrupt:
        ui.say("\nStopped. Running setup again continues from where it stopped.")
        return 130
    except Exception as exc:  # anything unexpected: plain words plus the log
        ui.log(__import__("traceback").format_exc())
        ui.show_failure(ui.Failed("DT-X99", f"Unexpected problem: {exc}",
                                  todo="Send the log file to whoever supports DiagTrack."), log)
        return ui.pause_exit(3)
