"""Offline behavior tests; all temporary artifacts remain inside the project."""
import contextlib
import csv
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
import urllib.error

import mail_behalf as app


GROUPS = {"1130000000000001": {"name": "Поддержка", "email": "support@example.invalid"}}
EID = next(iter(GROUPS))
UID = "1130000000000101"


class Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=app.ROOT)
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "groups.csv"
        self.state_path = self.path.with_suffix(".state.json")
        self.state = {"version": 1, "org_id": "123", "operation": "grant", "role": app.ROLE,
                      "groups": GROUPS, "results": {}}
        self.client = Mock(org="123")
        self.client.groups.return_value = (GROUPS, 0)
        self.output = contextlib.redirect_stdout(io.StringIO())
        self.output.__enter__()
        self.addCleanup(self.output.__exit__, None, None, None)

    def csv(self, uids=UID):
        app.write_csv(self.path, app.FIELDS, [{"name": "Поддержка", "emailId": EID, "uids": uids}])

    def apply(self, **kwargs):
        with patch("builtins.input", return_value="GRANT 1"):
            return app.apply_file(self.client, self.path, self.state_path, self.state, delay=0, **kwargs)

    def test_csv_unicode_bom_duplicates_spaces(self):
        self.csv(f" {UID}, {UID} ")
        self.assertTrue(self.path.read_bytes().startswith(b"\xef\xbb\xbf"))
        self.assertEqual(app.read_plan(self.path, GROUPS), ([(EID, UID)], 1))

    def test_long_ids_reject_rounding_notation_and_bad_lists(self):
        for value in ("1.13E+15", "1.0", "0", "-3", "１２３", "18446744073709551616", UID + ",", UID + ",,123"):
            with self.subTest(value=value):
                self.csv(value)
                with self.assertRaises(app.AppError):
                    app.read_plan(self.path, GROUPS)

    def test_blank_means_no_post(self):
        self.csv("")
        self.assertEqual(self.apply(), 0)
        self.client.grant.assert_not_called()

    def test_entire_csv_validated_before_post(self):
        self.csv(UID)
        with self.path.open("a", encoding="utf-8") as stream:
            stream.write(f"Поддержка;{EID};bad\n")
        with self.assertRaises(app.AppError):
            self.apply()
        self.client.grant.assert_not_called()

    def test_changed_id_or_name_rejected(self):
        for field, value in (("name", "Другая"), ("emailId", "123")):
            row = {"name": "Поддержка", "emailId": EID, "uids": UID}
            row[field] = value
            app.write_csv(self.path, app.FIELDS, [row])
            with self.assertRaises(app.AppError):
                app.read_plan(self.path, GROUPS)

    def test_dry_run_no_post_no_journal_mutation(self):
        self.csv()
        self.assertEqual(self.apply(dry_run=True), 0)
        self.client.grant.assert_not_called()
        self.assertFalse(self.state_path.exists())

    def test_confirmation_cancellation(self):
        self.csv()
        with patch("builtins.input", return_value="no"), self.assertRaises(app.AppError):
            app.apply_file(self.client, self.path, self.state_path, self.state)
        self.client.grant.assert_not_called()

    def test_success_persisted_and_resume_skips(self):
        self.csv()
        self.assertEqual(self.apply(), 0)
        self.client.grant.assert_called_once_with(EID, UID, "grant")
        loaded = app.load_state(self.state_path, "123", "grant")
        self.assertEqual(loaded["results"][f"{EID}:{UID}"]["status"], "success")
        self.assertEqual(self.apply(), 0)
        self.client.grant.assert_called_once()

    def test_intent_persisted_before_post(self):
        self.csv()
        def inspect(*args):
            loaded = app.load_state(self.state_path, "123", "grant")
            self.assertEqual(loaded["results"][f"{EID}:{UID}"]["status"], "pending")
        self.client.grant.side_effect = inspect
        self.assertEqual(self.apply(), 0)

    def test_network_unknown_requires_explicit_retry(self):
        self.csv()
        self.client.grant.side_effect = app.ApiError()
        self.assertEqual(self.apply(), 2)
        with self.assertRaises(app.AppError):
            self.apply()
        self.assertEqual(self.client.grant.call_count, 1)
        self.client.grant.side_effect = None
        self.assertEqual(self.apply(retry_uncertain=True), 0)
        self.assertEqual(self.client.grant.call_count, 2)

    def test_ctrl_c_leaves_pending_and_report(self):
        self.csv()
        self.client.grant.side_effect = KeyboardInterrupt
        with self.assertRaises(KeyboardInterrupt):
            self.apply()
        loaded = app.load_state(self.state_path, "123", "grant")
        self.assertEqual(loaded["results"][f"{EID}:{UID}"]["status"], "pending")
        self.assertTrue(self.path.with_suffix(".results.csv").exists())

    def test_403_stops_remaining(self):
        self.csv(UID + ",1130000000000102")
        self.client.grant.side_effect = app.ApiError(403)
        with patch("builtins.input", return_value="GRANT 2"):
            result = app.apply_file(self.client, self.path, self.state_path, self.state, delay=0)
        self.assertEqual(result, 2)
        self.client.grant.assert_called_once()
        with self.path.with_suffix(".results.csv").open(encoding="utf-8-sig") as stream:
            rows = list(csv.DictReader(stream, delimiter=";"))
        self.assertEqual([r["status"] for r in rows], ["failed", "not_started"])

    def test_400_continues_next_pair(self):
        self.csv(UID + ",1130000000000102")
        self.client.grant.side_effect = [app.ApiError(400), None]
        with patch("builtins.input", return_value="GRANT 2"):
            self.assertEqual(app.apply_file(self.client, self.path, self.state_path, self.state, delay=0), 2)
        self.assertEqual(self.client.grant.call_count, 2)

    def test_state_bound_to_org_and_operation(self):
        app.save_state(self.state_path, self.state)
        for org, operation in (("456", "grant"), ("123", "revoke")):
            with self.assertRaises(app.AppError):
                app.load_state(self.state_path, org, operation)

    def test_export_never_overwrites(self):
        self.csv()
        with self.assertRaises(app.AppError):
            app.export_file(self.client, self.path, self.state_path, "grant")
        self.client.groups.assert_not_called()

    def test_changed_directory_stops_before_post(self):
        self.csv()
        self.client.groups.return_value = ({}, 0)
        with self.assertRaises(app.AppError):
            self.apply()
        self.client.grant.assert_not_called()

    def test_pagination_and_exclusion(self):
        client = app.Client("test-only", "123")
        group = {"id": 1, "name": "Поддержка", "emailId": EID, "email": "support@example.invalid"}
        client.request = Mock(side_effect=[
            {"page": 1, "pages": 2, "groups": [group]},
            {"page": 2, "pages": 2, "groups": [dict(group, id=2, removed=True), {"id": 3}]}])
        self.assertEqual(client.groups(), (GROUPS, 2))
        self.assertIn("page=2", client.request.call_args.args[1])

    def test_repeated_page_is_rejected(self):
        client = app.Client("test-only", "123")
        client.request = Mock(return_value={"page": 1, "pages": 2, "groups": [{"id": 1}]})
        with self.assertRaises(app.AppError):
            client.groups()

    def test_post_exact_role_and_single_subject(self):
        client = app.Client("test-only", "123")
        client.request = Mock()
        client.grant(EID, UID, "grant")
        method, url, payload = client.request.call_args.args
        self.assertEqual(method, "POST")
        self.assertIn(f"/mail-lists/{EID}/update-permissions", url)
        self.assertEqual(payload, {"role_actions": [{"type": "grant", "roles": ["mail_list_half_sender"],
                         "subjects": [{"type": "user", "id": UID, "org_id": "123"}]}]})

    def test_204_empty_response_success(self):
        client = app.Client("test-only", "123")
        response = Mock(status=204)
        context = Mock()
        context.__enter__ = Mock(return_value=response)
        context.__exit__ = Mock(return_value=False)
        client.opener.open = Mock(return_value=context)
        self.assertIsNone(client.grant(EID, UID, "revoke"))
        response.read.assert_not_called()

    def test_get_retries_but_post_does_not(self):
        for method, attempts in (("GET", 4), ("POST", 1)):
            client = app.Client("test-only", "123")
            client.opener.open = Mock(side_effect=urllib.error.URLError("not logged"))
            with patch("mail_behalf.time.sleep"), self.assertRaises(app.ApiError):
                client.request(method, "https://example.invalid")
            self.assertEqual(client.opener.open.call_count, attempts)

    def test_get_429_honors_retry_after(self):
        client = app.Client("test-only", "123")
        def error(*args, **kwargs):
            raise urllib.error.HTTPError("https://example.invalid", 429, "limit", {"Retry-After": "4"}, io.BytesIO())
        client.opener.open = Mock(side_effect=error)
        with patch("mail_behalf.time.sleep") as sleep, self.assertRaises(app.ApiError):
            client.request("GET", "https://example.invalid")
        self.assertEqual(sleep.call_count, 3)
        sleep.assert_called_with(4)

    def test_interactive_cli_full_flow(self):
        root = Path(self.temp.name)
        responses = []
        def prompt(text):
            responses.append(text)
            if "Enter" in text:
                self.csv(UID)
                return ""
            return "GRANT 1"
        with patch.object(app, "ROOT", root), patch.object(app, "Client", return_value=self.client), \
                patch.dict(app.os.environ, {"OAUTH_TOKEN": "test-only"}), patch("builtins.input", side_effect=prompt):
            code = app.main(["--org-id", "123", "--file", "groups.csv", "--delay", "0"])
        self.assertEqual(code, 0)
        self.assertEqual(len(responses), 2)
        self.client.grant.assert_called_once_with(EID, UID, "grant")
        self.assertTrue(self.path.with_suffix(".results.csv").exists())
        self.assertFalse(self.path.with_suffix(".lock").exists())

    def test_cli_existing_lock_is_preserved(self):
        lock = self.path.with_suffix(".lock")
        lock.write_text("other-process")
        with patch.object(app, "ROOT", Path(self.temp.name)), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(app.main(["--org-id", "123", "--file", "groups.csv"]), 1)
        self.assertEqual(lock.read_text(), "other-process")
        self.client.grant.assert_not_called()

    def test_cli_rejects_path_outside_script_directory(self):
        with patch.object(app, "ROOT", Path(self.temp.name)), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(app.main(["--org-id", "123", "--file", "../outside.csv"]), 1)
        self.assertFalse((Path(self.temp.name).parent / "outside.csv").exists())

    def test_formula_names_are_escaped(self):
        self.assertEqual(app.display_name(" =SUM(1)"), "' =SUM(1)")


if __name__ == "__main__":
    unittest.main()
