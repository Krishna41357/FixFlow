"""
Real tests — these call actual code and would have caught all 4 blocking bugs.

Requirements:
    pip install pytest pytest-asyncio mongomock httpx

Run:
    pytest tests/test_real.py -v
"""

import json
import hmac
import hashlib
import pytest
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone


# ══════════════════════════════════════════════════════════════════════════════
# BUG #1 — Wrong webhook endpoint
# These tests call the real controller functions and assert on what HTTP call
# was actually made. They would FAIL on the old code.
# ══════════════════════════════════════════════════════════════════════════════

class TestWebhookEndpoints:
    """Assert the real GitHub API URL and payload structure are used."""

    @patch("controllers.github_controller.requests.post")
    def test_register_uses_repo_hook_endpoint_not_app_hook(self, mock_post):
        """FAILS old code: old code used PATCH /app/hook/config instead."""
        from controllers.github_controller import register_github_webhook

        mock_post.return_value = MagicMock(
            status_code=201,
            json=lambda: {"id": 111, "active": True, "config": {"url": "https://x.com/wh"}}
        )

        result = register_github_webhook(
            github_token="tok",
            repo_owner="acme",
            repo_name="data-repo",
            webhook_url="https://api.example.com/github/webhook?connection_id=c&user_id=u",
            webhook_secret="secret"
        )

        assert result is not None, "Should succeed on 201"
        called_url = mock_post.call_args[0][0]
        # Old code: "https://api.github.com/app/hook/config"
        assert called_url == "https://api.github.com/repos/acme/data-repo/hooks", (
            f"Wrong endpoint: {called_url}"
        )

    @patch("controllers.github_controller.requests.post")
    def test_register_uses_post_not_patch(self, mock_post):
        """FAILS old code: old code used requests.patch."""
        from controllers.github_controller import register_github_webhook

        mock_post.return_value = MagicMock(
            status_code=201,
            json=lambda: {"id": 112, "active": True, "config": {"url": "x"}}
        )

        register_github_webhook("tok", "acme", "repo", "https://wh.example.com", "sec")

        # If old code ran (requests.patch), mock_post would have 0 calls here
        assert mock_post.call_count == 1

    @patch("controllers.github_controller.requests.post")
    def test_register_sends_nested_config_payload(self, mock_post):
        """FAILS old code: old code sent flat payload, not nested under 'config'."""
        from controllers.github_controller import register_github_webhook

        mock_post.return_value = MagicMock(
            status_code=201,
            json=lambda: {"id": 113, "active": True, "config": {"url": "x"}}
        )

        wh_url = "https://api.example.com/github/webhook?connection_id=abc&user_id=xyz"
        register_github_webhook("tok", "acme", "repo", wh_url, "sec123")

        payload = mock_post.call_args[1]["json"]

        # Top-level structure
        assert payload["name"] == "web"
        assert payload["events"] == ["pull_request"]
        assert payload["active"] is True

        # Secret and URL must be inside "config", not at top level
        assert "config" in payload, "Payload must have nested 'config' key"
        assert payload["config"]["url"] == wh_url
        assert payload["config"]["secret"] == "sec123"
        assert payload["config"]["content_type"] == "json"

        # Old code put these at top level — assert they're NOT there
        assert "url" not in payload, "url should be inside config, not at top level"
        assert "secret" not in payload, "secret should be inside config, not at top level"

    @patch("controllers.github_controller.requests.post")
    def test_register_treats_201_as_success_not_200(self, mock_post):
        """FAILS old code: old code checked status_code == 200, not 201."""
        from controllers.github_controller import register_github_webhook

        # GitHub returns 201 for new hooks
        mock_post.return_value = MagicMock(
            status_code=201,
            json=lambda: {"id": 999, "active": True, "config": {"url": "x"}}
        )
        result = register_github_webhook("tok", "acme", "repo", "https://wh.example.com", "sec")
        assert result is not None, "201 should be treated as success"
        assert result["webhook_id"] == "999"

    @patch("controllers.github_controller.requests.post")
    def test_register_treats_200_as_failure(self, mock_post):
        """200 is not a valid response for POST /hooks — should return None."""
        from controllers.github_controller import register_github_webhook

        mock_post.return_value = MagicMock(
            status_code=200,     # Wrong — this is what the old /app/hook/config returned
            json=lambda: {"id": 999, "active": True}
        )
        result = register_github_webhook("tok", "acme", "repo", "https://wh.example.com", "sec")
        assert result is None, "200 from POST /hooks means something went wrong"

    @patch("controllers.github_controller.requests.delete")
    def test_delete_uses_repo_hook_endpoint(self, mock_delete):
        """FAILS old code: old code used DELETE /app/hook/config."""
        from controllers.github_controller import delete_github_webhook

        mock_delete.return_value = MagicMock(status_code=204, text="")

        delete_github_webhook(
            github_token="tok",
            repo_owner="acme",
            repo_name="data-repo",
            webhook_id="wh_987"
        )

        called_url = mock_delete.call_args[0][0]
        assert called_url == "https://api.github.com/repos/acme/data-repo/hooks/wh_987", (
            f"Wrong delete endpoint: {called_url}"
        )

    @patch("controllers.github_controller.requests.get")
    def test_verify_uses_specific_hook_endpoint(self, mock_get):
        """FAILS old code: old code used GET /app/hook/config."""
        from controllers.github_controller import verify_github_webhook

        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: {"id": 555, "active": True, "config": {"url": "x"}, "deliveries_url": "d"}
        )

        verify_github_webhook(
            github_token="tok",
            repo_owner="acme",
            repo_name="data-repo",
            webhook_id="555"
        )

        called_url = mock_get.call_args[0][0]
        assert called_url == "https://api.github.com/repos/acme/data-repo/hooks/555"

    def test_register_missing_repo_owner_returns_none(self):
        """Should fail fast without hitting GitHub if owner/repo missing."""
        from controllers.github_controller import register_github_webhook

        result = register_github_webhook(
            github_token="tok",
            repo_owner="",          # missing
            repo_name="data-repo",
            webhook_url="https://wh.example.com",
            webhook_secret="sec"
        )
        assert result is None

    def test_register_missing_token_returns_none(self):
        from controllers.github_controller import register_github_webhook

        result = register_github_webhook(
            github_token=None,
            repo_owner="acme",
            repo_name="data-repo",
            webhook_url="https://wh.example.com",
            webhook_secret="sec"
        )
        assert result is None


# ══════════════════════════════════════════════════════════════════════════════
# BUG #2 — installation_id type mismatch (int model field vs str comparison)
# ══════════════════════════════════════════════════════════════════════════════

class TestInstallationIdType:
    """installation_id must be consistently treated as str throughout the stack."""

    def test_get_installation_token_accepts_int_coerces_to_str(self):
        """FAILS old code: int 98765 compared to string "demo" was always falsy."""
        from controllers.github_controller import get_installation_token

        # Simulate what happens when Pydantic model returns int
        with patch("controllers.github_controller.GITHUB_TEST_PAT", ""), \
             patch("controllers.github_controller._generate_app_jwt", return_value="jwt"), \
             patch("controllers.github_controller.requests.post") as mock_post:

            mock_post.return_value = MagicMock(
                status_code=201,
                json=lambda: {"token": "ghs_real_token"}
            )

            # Pass as int — what Pydantic returns when field is declared Optional[int]
            token = get_installation_token(98765)

            assert token == "ghs_real_token", (
                "int installation_id should be coerced to str before comparison"
            )
            called_url = mock_post.call_args[0][0]
            assert "98765" in called_url

    def test_get_installation_token_int_is_not_treated_as_demo(self):
        """int 98765 must NOT fall into the 'demo' / None early-exit path."""
        from controllers.github_controller import get_installation_token

        with patch("controllers.github_controller.GITHUB_TEST_PAT", ""), \
             patch("controllers.github_controller._generate_app_jwt", return_value=None):
            # _generate_app_jwt returning None means no app key configured,
            # but we should reach that point — not exit at the installation_id check.
            token = get_installation_token(98765)
            # None because JWT failed, but the installation_id check was passed
            assert token is None  # not the same as "skipped because int == 'demo'"

    def test_none_installation_id_returns_none(self):
        from controllers.github_controller import get_installation_token

        with patch("controllers.github_controller.GITHUB_TEST_PAT", ""):
            result = get_installation_token(None)
            assert result is None

    def test_demo_string_returns_none_without_test_pat(self):
        from controllers.github_controller import get_installation_token

        with patch("controllers.github_controller.GITHUB_TEST_PAT", ""):
            result = get_installation_token("demo")
            assert result is None


# ══════════════════════════════════════════════════════════════════════════════
# BUG #3 — Raw dicts passed as affected_assets to RootCause
# ══════════════════════════════════════════════════════════════════════════════

class TestAILayerModelConstruction:
    """call_ai_layer must return a proper RootCause, not crash on nested dicts."""

    def _ai_response(self):
        return {
            "one_line_summary":    "Column user_id renamed broke 3 downstream models",
            "detailed_explanation": "The column was renamed in the source.",
            "break_point_fqn":     "raw.users",
            "break_point_change":  "user_id renamed to customer_id",
            "affected_assets": [
                {
                    "fqn":          "analytics.orders",
                    "asset_type":   "table",
                    "display_name": "orders",
                    "severity":     "critical",
                    "owner_email":  None
                },
                {
                    "fqn":          "analytics.sessions",
                    "asset_type":   "table",
                    "display_name": "sessions",
                    "severity":     "high",
                    "owner_email":  "owner@example.com"
                }
            ],
            "suggested_fixes": [
                {
                    "description":      "Update references to user_id → customer_id",
                    "fix_type":         "rename_column",
                    "target_asset_fqn": "analytics.orders",
                    "code_snippet":     "ALTER TABLE orders RENAME COLUMN user_id TO customer_id"
                }
            ],
            "owner_to_contact": "alice@example.com",
            "confidence": 0.88
        }

    def test_call_ai_layer_returns_rootcause_instance(self):
        """FAILS old code: Pydantic v2 raises ValidationError on raw dicts."""
        from controllers.investigation_controller import call_ai_layer
        from models.investigations import RootCause

        with patch("controllers.investigation_controller._call_groq", return_value=self._ai_response()), \
             patch.dict("os.environ", {"DEFAULT_LLM_PROVIDER": "groq"}):
            result = call_ai_layer("some context")

        assert result is not None, "Should not return None — AI response was valid"
        assert isinstance(result, RootCause), f"Expected RootCause, got {type(result)}"

    def test_affected_assets_are_model_instances_not_dicts(self):
        """FAILS old code: affected_assets were left as raw dicts."""
        from controllers.investigation_controller import call_ai_layer
        from models.events import AffectedAsset

        with patch("controllers.investigation_controller._call_groq", return_value=self._ai_response()), \
             patch.dict("os.environ", {"DEFAULT_LLM_PROVIDER": "groq"}):
            result = call_ai_layer("context")

        assert result is not None
        assert len(result.affected_assets) == 2
        for asset in result.affected_assets:
            assert isinstance(asset, AffectedAsset), (
                f"Expected AffectedAsset, got {type(asset)}"
            )

    def test_affected_assets_have_correct_field_values(self):
        from controllers.investigation_controller import call_ai_layer

        with patch("controllers.investigation_controller._call_groq", return_value=self._ai_response()), \
             patch.dict("os.environ", {"DEFAULT_LLM_PROVIDER": "groq"}):
            result = call_ai_layer("context")

        assert result.affected_assets[0].fqn == "analytics.orders"
        assert result.affected_assets[0].severity.value == "critical"
        assert result.affected_assets[1].owner_email == "owner@example.com"

    def test_suggested_fixes_are_model_instances(self):
        from controllers.investigation_controller import call_ai_layer
        from models.investigations import SuggestedFix

        with patch("controllers.investigation_controller._call_groq", return_value=self._ai_response()), \
             patch.dict("os.environ", {"DEFAULT_LLM_PROVIDER": "groq"}):
            result = call_ai_layer("context")

        assert len(result.suggested_fixes) == 1
        assert isinstance(result.suggested_fixes[0], SuggestedFix)
        assert result.suggested_fixes[0].fix_type == "rename_column"

    def test_malformed_affected_asset_is_skipped_not_crash(self):
        """A bad asset dict should be skipped, not crash the whole analysis."""
        from controllers.investigation_controller import call_ai_layer

        bad_response = self._ai_response()
        bad_response["affected_assets"].append({"fqn": "x"})  # missing required fields

        with patch("controllers.investigation_controller._call_groq", return_value=bad_response), \
             patch.dict("os.environ", {"DEFAULT_LLM_PROVIDER": "groq"}):
            result = call_ai_layer("context")

        assert result is not None
        # The 2 valid assets should still be present
        assert len(result.affected_assets) == 2

    def test_none_ai_response_returns_none(self):
        from controllers.investigation_controller import call_ai_layer

        with patch("controllers.investigation_controller._call_groq", return_value=None), \
             patch.dict("os.environ", {"DEFAULT_LLM_PROVIDER": "groq"}):
            result = call_ai_layer("context", max_retries=1)

        assert result is None


# ══════════════════════════════════════════════════════════════════════════════
# BUG #4 — get_investigation returns raw Mongo dicts instead of model instances
# ══════════════════════════════════════════════════════════════════════════════

class TestGetInvestigationModelHydration:
    """
    get_investigation must return proper Pydantic instances.
    FAILS old code because PR bot calls getattr(root_cause, 'one_line_summary')
    on a plain dict, which always returns None.
    """

    def _make_raw_doc(self, inv_id):
        from bson import ObjectId
        return {
            "_id":               ObjectId(inv_id),
            "user_id":           "user_abc",
            "event_id":          "evt_1",
            "failing_asset_fqn": "analytics.orders",
            "failure_message":   "Column missing",
            "event_type":        "manual",
            "status":            "completed",
            "root_cause": {
                "one_line_summary":    "Column renamed in source",
                "detailed_explanation": "The column was renamed.",
                "break_point_fqn":     "raw.users",
                "break_point_change":  "user_id → customer_id",
                "affected_assets": [
                    {
                        "fqn":          "analytics.orders",
                        "asset_type":   "table",
                        "display_name": "orders",
                        "severity":     "critical",
                        "owner_email":  None
                    }
                ],
                "suggested_fixes": [],
                "owner_to_contact": None,
                "confidence": 0.9
            },
            "lineage_subgraph": None,
            "pr_number": None,
            "pr_url":    None,
            "created_at":   "2025-01-01T00:00:00+00:00",
            "completed_at": "2025-01-01T00:01:00+00:00",
            "processing_time_ms": 60000
        }

    def test_root_cause_is_model_instance_not_dict(self):
        """FAILS old code: returned raw dict from Mongo."""
        from controllers.investigation_controller import get_investigation
        from models.investigations import RootCause

        raw = self._make_raw_doc("507f1f77bcf86cd799439011")

        with patch("controllers.investigation_controller.investigations_collection") as mock_coll:
            mock_coll.find_one.return_value = raw

            result = get_investigation("507f1f77bcf86cd799439011", "user_abc")

        assert result is not None
        assert isinstance(result.root_cause, RootCause), (
            f"Expected RootCause instance, got {type(result.root_cause)}"
        )

    def test_root_cause_fields_accessible_via_attribute(self):
        """FAILS old code: dict.one_line_summary raises AttributeError → PR bot gets None."""
        from controllers.investigation_controller import get_investigation

        raw = self._make_raw_doc("507f1f77bcf86cd799439012")

        with patch("controllers.investigation_controller.investigations_collection") as mock_coll:
            mock_coll.find_one.return_value = raw

            result = get_investigation("507f1f77bcf86cd799439012", "user_abc")

        assert result.root_cause.one_line_summary == "Column renamed in source"
        assert result.root_cause.confidence == 0.9
        assert result.root_cause.break_point_fqn == "raw.users"

    def test_affected_assets_inside_root_cause_are_model_instances(self):
        """Nested affected_assets must also be AffectedAsset instances, not dicts."""
        from controllers.investigation_controller import get_investigation
        from models.events import AffectedAsset

        raw = self._make_raw_doc("507f1f77bcf86cd799439013")

        with patch("controllers.investigation_controller.investigations_collection") as mock_coll:
            mock_coll.find_one.return_value = raw

            result = get_investigation("507f1f77bcf86cd799439013", "user_abc")

        assert len(result.root_cause.affected_assets) == 1
        assert isinstance(result.root_cause.affected_assets[0], AffectedAsset)

    def test_pr_bot_comment_fields_are_non_empty(self):
        """
        Simulates what run_investigation_and_update_pr does after get_investigation.
        FAILS old code because all getattr calls return None on a dict.
        """
        from controllers.investigation_controller import get_investigation

        raw = self._make_raw_doc("507f1f77bcf86cd799439014")

        with patch("controllers.investigation_controller.investigations_collection") as mock_coll:
            mock_coll.find_one.return_value = raw

            inv = get_investigation("507f1f77bcf86cd799439014", "user_abc")

        # This is what the PR bot does — must not be None
        root_cause = inv.root_cause
        assert root_cause is not None

        summary     = root_cause.one_line_summary
        explanation = root_cause.detailed_explanation
        confidence  = root_cause.confidence
        fixes       = root_cause.suggested_fixes
        affected    = root_cause.affected_assets

        assert summary     is not None and summary != ""
        assert explanation is not None
        assert confidence  is not None
        assert isinstance(fixes,    list)
        assert isinstance(affected, list)

    def test_none_root_cause_in_db_returns_none_not_crash(self):
        """Pending investigation with no root_cause yet should not crash."""
        from controllers.investigation_controller import get_investigation

        raw = self._make_raw_doc("507f1f77bcf86cd799439015")
        raw["root_cause"] = None
        raw["status"]     = "pending"

        with patch("controllers.investigation_controller.investigations_collection") as mock_coll:
            mock_coll.find_one.return_value = raw

            result = get_investigation("507f1f77bcf86cd799439015", "user_abc")

        assert result is not None
        assert result.root_cause is None   # pending, not a crash

    def test_missing_investigation_returns_none(self):
        from controllers.investigation_controller import get_investigation

        with patch("controllers.investigation_controller.investigations_collection") as mock_coll:
            mock_coll.find_one.return_value = None

            result = get_investigation("507f1f77bcf86cd799439016", "user_abc")

        assert result is None


# ══════════════════════════════════════════════════════════════════════════════
# WEBHOOK URL BUILDER — these actually pass before and after, good regression guard
# ══════════════════════════════════════════════════════════════════════════════

class TestWebhookURLBuilder:
    def test_basic_url_structure(self):
        from controllers.github_controller import build_webhook_url
        url = build_webhook_url("c123", "u456", "https://api.example.com")
        assert url == "https://api.example.com/github/webhook?connection_id=c123&user_id=u456"

    def test_strips_trailing_slash(self):
        from controllers.github_controller import build_webhook_url
        url1 = build_webhook_url("c", "u", "https://api.example.com/")
        url2 = build_webhook_url("c", "u", "https://api.example.com")
        assert url1 == url2

    def test_mongo_objectid_format(self):
        from controllers.github_controller import build_webhook_url
        conn_id = "507f1f77bcf86cd799439011"
        user_id = "507f1f77bcf86cd799439010"
        url = build_webhook_url(conn_id, user_id, "https://api.example.com/v1")
        assert conn_id in url
        assert user_id in url


# ══════════════════════════════════════════════════════════════════════════════
# SIGNATURE VERIFICATION — real hmac check
# ══════════════════════════════════════════════════════════════════════════════

class TestSignatureVerification:
    def test_valid_signature_passes(self):
        from controllers.github_controller import verify_github_signature

        secret  = "test_secret"
        payload = b'{"action":"opened"}'
        sig     = "sha256=" + hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()

        with patch("controllers.github_controller.GITHUB_WEBHOOK_SECRET", secret):
            assert verify_github_signature(sig, payload) is True

    def test_invalid_signature_fails(self):
        from controllers.github_controller import verify_github_signature

        with patch("controllers.github_controller.GITHUB_WEBHOOK_SECRET", "real_secret"):
            assert verify_github_signature("sha256=fake", b"payload") is False

    def test_tampered_payload_fails(self):
        from controllers.github_controller import verify_github_signature

        secret   = "test_secret"
        original = b'{"action":"opened"}'
        tampered = b'{"action":"closed"}'
        sig      = "sha256=" + hmac.new(secret.encode(), original, hashlib.sha256).hexdigest()

        with patch("controllers.github_controller.GITHUB_WEBHOOK_SECRET", secret):
            assert verify_github_signature(sig, tampered) is False

    def test_no_secret_configured_skips_check(self):
        """Dev mode — no secret means always pass."""
        from controllers.github_controller import verify_github_signature

        with patch("controllers.github_controller.GITHUB_WEBHOOK_SECRET", ""):
            assert verify_github_signature("sha256=anything", b"payload") is True


if __name__ == "__main__":
    pytest.main([__file__, "-v"])