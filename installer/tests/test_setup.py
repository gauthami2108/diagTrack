"""Tests of the setup program that run on any system (python -m unittest discover installer/tests)."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "installer"))
sys.path.insert(0, str(ROOT / "app"))

from diagtrack_setup import common, docker_mode, ui, windows  # noqa: E402


class UiTests(unittest.TestCase):
    def tearDown(self):
        ui.ANSWERS.clear()

    def test_password_rules(self):
        self.assertIsNotNone(ui.password_problem("short1"))
        self.assertIsNotNone(ui.password_problem("ChangeMe@123"))
        self.assertIsNotNone(ui.password_problem("onlyletterslong"))
        self.assertIsNone(ui.password_problem("Pramodini2026x"))
        self.assertIsNotNone(common.passphrase_problem("short"))
        self.assertIsNone(common.passphrase_problem("blue lamps over kukatpally"))

    def test_unattended_answers_and_secrets(self):
        ui.ANSWERS.update({"port": "8080", "confirm": "yes", "mode": "move"})
        self.assertEqual(ui.ask("Port", "80", key="port"), "8080")
        self.assertTrue(ui.yes("Go?", False, key="confirm"))
        self.assertEqual(ui.choose("Mode?", [("new", "New"), ("move", "Move")], key="mode"), "move")
        with mock.patch.dict(os.environ, {"DT_ADMIN_PASSWORD": "Pramodini2026x"}):
            self.assertEqual(ui.ask_secret("Password", check=ui.password_problem, key="admin_password"), "Pramodini2026x")
        with mock.patch.dict(os.environ, {"DT_ADMIN_PASSWORD": "weak"}):
            with self.assertRaises(ui.Failed):
                ui.ask_secret("Password", check=ui.password_problem, key="admin_password")

    def test_log_never_contains_secrets(self):
        with tempfile.TemporaryDirectory() as d:
            ui.start_log(Path(d) / "setup.log")
            ui.hide("Sup3r-Secret-Value")
            ui.log("connecting with Sup3r-Secret-Value now")
            text = (Path(d) / "setup.log").read_text()
            self.assertNotIn("Sup3r-Secret-Value", text)
            self.assertIn("****", text)


class MoveFileTests(unittest.TestCase):
    def test_read_move_file_without_any_path_set_up(self):
        import io
        import subprocess
        code = (
            "import sys, io; sys.path.insert(0, %r)\n"
            "from diagtrack_setup import common, ui\n"
            "try:\n    common.read_move_file(%r, 'x' * 12)\n"
            "except ui.Failed as f:\n    print('FAILED', f.code)\n"
        ) % (str(ROOT / "installer"), str(ROOT / "installer" / "versions.json"))
        out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd="/")
        self.assertIn("FAILED DT-M02", out.stdout, out.stderr)


class WindowsPieceTests(unittest.TestCase):
    def test_service_definition(self):
        xml = windows.service_xml({"service": "postgresql-x64-17"})
        self.assertIn("<id>DiagTrack</id>", xml)
        self.assertIn("serve</arguments>", xml)
        self.assertIn("<depend>postgresql-x64-17</depend>", xml)
        self.assertIn('<onfailure action="restart"', xml)
        self.assertIn("DIAGTRACK_HOME", xml)

    def test_versions_are_pinned_and_signed(self):
        v = windows.versions()
        self.assertTrue(v["python"]["url"].startswith("https://www.nuget.org/"))
        self.assertTrue(v["python"]["version"].startswith("3.13."))
        self.assertTrue(v["postgresql"]["version"].startswith("17."))
        self.assertTrue(all(u.startswith("https://") for u in v["postgresql"]["urls"]))
        self.assertTrue((ROOT / "installer" / "windows" / "DiagTrackService.exe").stat().st_size > 100_000)


class DockerSetupTests(unittest.TestCase):
    def test_new_install_writes_settings_and_secrets(self):
        with tempfile.TemporaryDirectory() as d:
            project = Path(d)
            (project / "docker-compose.yml").write_text("name: diagtrack\n")
            with mock.patch.object(docker_mode, "PROJECT", project), \
                    mock.patch.object(docker_mode, "SECRETS", project / "secrets"), \
                    mock.patch.object(docker_mode, "STATUS", project / "data" / "status"), \
                    mock.patch.dict(os.environ, {"DT_ADMIN_PASSWORD": "Pramodini2026x",
                                                 "DT_BACKUP_PASSPHRASE": "blue lamps over kukatpally"}):
                ui.ANSWERS.update({"mode": "new", "admin_user": "admin", "port": "80", "site_url": "http://dt",
                                   "backup1_path": "./data/backups", "backup2_wanted": "no", "backup_time": "01:30",
                                   "confirm": "yes"})
                try:
                    self.assertEqual(docker_mode.install(), 0)
                finally:
                    ui.ANSWERS.clear()
            names = sorted(p.name for p in (project / "secrets").iterdir())
            self.assertEqual(names, ["admin_password_hash", "backup_passphrase", "db_password", "field_key", "secret_key"])
            env = (project / ".env").read_text()
            self.assertNotIn("Pramodini2026x", env)
            self.assertNotIn("blue lamps", env)
            cfg = json.loads((project / "data" / "status" / "backup-config.json").read_text())
            self.assertEqual(cfg["destinations"][0]["path"], "/backups")
            self.assertTrue((project / "secrets" / "admin_password_hash").read_text().startswith("pbkdf2_sha256$"))

    def test_upgrade_of_the_first_docker_version_keeps_its_passwords(self):
        with tempfile.TemporaryDirectory() as d:
            project = Path(d)
            (project / "docker-compose.yml").write_text("name: diagtrack\n")
            (project / "data" / "postgres").mkdir(parents=True)
            (project / "data" / "postgres" / "PG_VERSION").write_text("17\n")
            (project / "data" / "status").mkdir(parents=True)
            (project / "data" / "status" / ".secret_key").write_text("old-session-key\n")
            (project / ".env").write_text("SECRET_KEY=\nDB_PASSWORD=old-db-pass\nADMIN_USERNAME=admin\n"
                                          "ADMIN_PASSWORD=ChangeMe@123\nBACKUP_DIR=/mnt/nas\nBACKUP_TIME=02:15\n"
                                          "BACKUP_KEEP_DAYS=30\nBACKUP_REQUIRE_MARKER=1\nHTTP_PORT=8080\n"
                                          "HTTPS_MODE=\nTIME_ZONE=Asia/Kolkata\n")
            with mock.patch.object(docker_mode, "PROJECT", project), \
                    mock.patch.object(docker_mode, "SECRETS", project / "secrets"), \
                    mock.patch.object(docker_mode, "STATUS", project / "data" / "status"), \
                    mock.patch.dict(os.environ, {"DT_BACKUP_PASSPHRASE": "blue lamps over kukatpally"}):
                ui.ANSWERS.update({"site_url": "http://dt", "backup2_wanted": "no", "confirm": "yes"})
                try:
                    self.assertEqual(docker_mode.install(), 0)
                finally:
                    ui.ANSWERS.clear()
            secrets_dir = project / "secrets"
            self.assertEqual((secrets_dir / "db_password").read_text().strip(), "old-db-pass")
            self.assertEqual((secrets_dir / "secret_key").read_text().strip(), "old-session-key")
            self.assertFalse((secrets_dir / "admin_password_hash").exists())
            env = (project / ".env").read_text()
            for gone in ("DB_PASSWORD", "SECRET_KEY", "ADMIN_PASSWORD", "ChangeMe", "BACKUP_TIME"):
                self.assertNotIn(gone, env)
            self.assertIn("HTTP_PORT=8080", env)
            self.assertIn("BACKUP_DIR=/mnt/nas", env)
            dest = json.loads((project / "data" / "status" / "backup-config.json").read_text())["destinations"][0]
            self.assertEqual((dest["time"], dest["keep_daily"], dest["require_marker"]), ("02:15", 30, True))


if __name__ == "__main__":
    unittest.main()


class ProgramSwitchTests(unittest.TestCase):
    """Replacing the program folder must survive a folder that Windows briefly refuses to rename."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.program, self.source = root / "program", root / "source"
        (self.program / "app").mkdir(parents=True)
        (self.program / "app" / "VERSION").write_text("old")
        (self.source / "app").mkdir(parents=True)
        (self.source / "app" / "VERSION").write_text("new")
        (self.source / "installer" / "windows").mkdir(parents=True)
        (self.source / "installer" / "windows" / "DiagTrackService.exe").write_text("exe")
        for patch in (mock.patch.object(windows, "PROGRAM", self.program), mock.patch.object(windows.time, "sleep"),
                      mock.patch.object(windows.ui, "log")):
            patch.start()
            self.addCleanup(patch.stop)

    def refuse(self, times):
        real, calls = Path.rename, {"n": 0}

        def rename(path, target):
            calls["n"] += 1
            if calls["n"] <= times:
                raise PermissionError(5, "Access is denied")
            return real(path, target)
        return mock.patch.object(Path, "rename", rename)

    def version(self, folder):
        return (self.program / folder / "VERSION").read_text()

    def test_normal_switch_keeps_the_old_version_as_previous(self):
        windows.copy_program(self.source)
        self.assertEqual((self.version("app"), self.version("app.previous")), ("new", "old"))

    def test_briefly_locked_folder_is_retried(self):
        with self.refuse(2):
            windows.copy_program(self.source)
        self.assertEqual(self.version("app"), "new")

    def test_folder_that_stays_locked_gives_a_clear_message_and_changes_nothing(self):
        with self.refuse(99):
            with self.assertRaises(ui.Failed) as ctx:
                windows.copy_program(self.source)
        self.assertEqual(ctx.exception.code, "DT-U05")
        self.assertIn("Close every window", ctx.exception.todo)
        self.assertEqual(self.version("app"), "old")  # the installed version is untouched

    def test_failure_after_the_old_folder_moved_puts_it_back(self):
        real, calls = Path.rename, {"n": 0}

        def rename(path, target):
            calls["n"] += 1
            if path.name == "app.new":
                raise PermissionError(5, "Access is denied")
            return real(path, target)
        with mock.patch.object(Path, "rename", rename):
            with self.assertRaises(ui.Failed):
                windows.copy_program(self.source)
        self.assertEqual(self.version("app"), "old")

    def test_failed_update_starts_the_installed_version_again(self):
        with mock.patch.object(windows, "load_state", return_value={"port": 80, "version": "1"}), \
                mock.patch.object(windows.ui, "yes", return_value=True), \
                mock.patch.object(windows, "manage", return_value=mock.Mock(returncode=0, stdout="")), \
                mock.patch.object(windows, "DATA", self.program / "data"), \
                mock.patch.object(windows, "stop_service") as stop, \
                mock.patch.object(windows, "start_service") as start, \
                mock.patch.object(windows, "copy_program", side_effect=ui.Failed("DT-U05", "locked")):
            with self.assertRaises(ui.Failed):
                windows.update(self.source)
        stop.assert_called_once()
        start.assert_called_once_with(80)
