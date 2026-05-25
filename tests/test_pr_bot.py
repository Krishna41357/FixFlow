"""
tests/test_pr_bot.py — FixFlow PR Bot Test Suite

Covers every feature implemented in the PR bot workflow:

  1.  File filter             — _is_relevant_yml, filter_relevant_files
  2.  Context line stripping  — _strip_context_lines, _warn_large_patch
  3.  SQL FQN extraction      — _extract_fqn_from_sql
  4.  YML FQN extraction      — _extract_fqn_from_yml (single + multi-model)
  5.  derive_fqns             — composite key for multi-model ymls
  6.  Signature verification  — verify_github_signature
  7.  Webhook URL builder     — build_webhook_url
  8.  Comment renderer        — render_placeholder_comment, render_pr_comment
  9.  Lineage merge           — merge_lineage_subgraphs
  10. AI prompt builder       — build_pr_ai_context
  11. AI response parsing     — _parse_pr_ai_response
  12. Investigation creation  — create_investigation (event_type param)
  13. PR root cause deserialise — _deserialise_pr_root_cause
  14. Webhook handler         — github_pr_webhook (FastAPI route, via TestClient)
  15. Integration             — full flow with mocked dependencies
"""

import hashlib
import hmac
import json
import pytest
from unittest.mock import patch, MagicMock, call
from typing import List, Optional

# ── Controllers under test ─────────────────────────────────────────────────────
from controllers.github_controller import (
    _is_relevant_yml,
    filter_relevant_files,
    _strip_context_lines,
    _warn_large_patch,
    _extract_fqn_from_sql,
    _extract_fqn_from_yml,
    derive_fqns,
    verify_github_signature,
    build_webhook_url,
    render_placeholder_comment,
    render_pr_comment,
)
from controllers.investigation_controller import (
    merge_lineage_subgraphs,
    build_pr_ai_context,
    _parse_pr_ai_response,
    _deserialise_pr_root_cause,
)

# ── Models ────────────────────────────────────────────────────────────────────
from models.github import (
    ChangedAsset,
    PRRootCause,
    ChangedAssetSummary,
    DownstreamImpact,
    AssetCause,
    ErrorLocation,
    CauseFix,
)
from models.lineage import LineageSubgraph, LineageNode, LineageEdge
from models.base import SeverityLevel, AssetType


# ═════════════════════════════════════════════════════════════════════════════
# Fixtures
# ═════════════════════════════════════════════════════════════════════════════

def make_asset(filename: str, patch: Optional[str] = "", status: str = "modified") -> ChangedAsset:
    if patch is None:
        patch = ""
    lines = patch.splitlines()
    additions = sum(1 for l in lines if l.startswith("+") and not l.startswith("+++"))
    deletions  = sum(1 for l in lines if l.startswith("-") and not l.startswith("---"))
    return ChangedAsset(
        filename=filename,
        status=status,
        additions=additions,
        deletions=deletions,
        changes=additions + deletions,
        patch=patch or None,
    )


SQL_PATCH_SIMPLE = """\
@@ -10,7 +10,7 @@
 SELECT
-  user_id INT,
+  user_id BIGINT,
   email VARCHAR(255),
   created_at TIMESTAMP
"""

YML_PATCH_SINGLE = """\
@@ -1,8 +1,9 @@
 version: 2
 models:
   - name: orders
     columns:
-      - name: user_id
-        data_type: int
+      - name: user_id
+        data_type: bigint
"""

YML_PATCH_MULTI = """\
@@ -1,15 +1,16 @@
 version: 2
 models:
   - name: orders
     columns:
       - name: order_id
   - name: customers
     columns:
-      - name: id
+      - name: customer_id
   - name: revenue
     columns:
       - name: amount
"""

YML_PATCH_SOURCE = """\
+version: 2
+sources:
+  - name: raw_stripe
+    tables:
+      - name: charges
"""


def make_pr_root_cause() -> PRRootCause:
    """Builds a complete PRRootCause for renderer/serialisation tests."""
    return PRRootCause(
        pr_summary="Two schema changes will break 2 downstream assets",
        overall_severity=SeverityLevel.HIGH,
        safe_to_merge=False,
        confidence=0.88,
        changed_assets=[
            ChangedAssetSummary(
                fqn="finance.revenue",
                filename="models/finance/revenue.sql",
                change_type="column_type_changed",
                change_description="user_id changed from INT to BIGINT",
                patch_evidence="-  user_id INT,\n+  user_id BIGINT,",
                fqn_approximate=False,
            ),
            ChangedAssetSummary(
                fqn="finance.orders",
                filename="models/finance/schema.yml::finance.orders",
                change_type="column_dropped",
                change_description="Column gross_margin dropped",
                patch_evidence="-      - name: gross_margin",
                fqn_approximate=False,
            ),
        ],
        downstream_impacts=[
            DownstreamImpact(
                fqn="reporting.daily_revenue",
                display_name="Daily Revenue Report",
                severity=SeverityLevel.CRITICAL,
                causes=[
                    AssetCause(
                        source_asset_fqn="finance.revenue",
                        error_type="type_mismatch",
                        error_description="JOIN on user_id will fail — INT vs BIGINT mismatch",
                        error_location=ErrorLocation(
                            file="models/reporting/daily_revenue.sql",
                            clause="JOIN",
                            approximate_line=22,
                        ),
                        fix=CauseFix(
                            description="Cast user_id to BIGINT in the JOIN",
                            fix_type="add_cast",
                            target_file="models/reporting/daily_revenue.sql",
                            code_snippet="JOIN finance.revenue ON CAST(r.user_id AS BIGINT) = o.user_id",
                        ),
                    ),
                    AssetCause(
                        source_asset_fqn="finance.orders",
                        error_type="missing_column",
                        error_description="References gross_margin which no longer exists",
                        error_location=ErrorLocation(
                            file="models/reporting/daily_revenue.sql",
                            clause="SELECT",
                            approximate_line=14,
                        ),
                        fix=CauseFix(
                            description="Remove gross_margin reference from SELECT",
                            fix_type="update_sql_ref",
                            target_file="models/reporting/daily_revenue.sql",
                            code_snippet="-- Remove line 14: gross_margin,",
                        ),
                    ),
                ],
            ),
        ],
    )


def make_lineage_node(fqn: str, is_break_point: bool = False, severity: SeverityLevel = SeverityLevel.MEDIUM) -> LineageNode:
    return LineageNode(
        fqn=fqn,
        display_name=fqn.split(".")[-1],
        asset_type=AssetType.TABLE,
        service_name="snowflake",
        depth_from_failure=0,
        is_break_point=is_break_point,
        severity=severity,
    )


def make_subgraph(failing_fqn: str, nodes: List[LineageNode], source_fqn: str) -> tuple:
    sg = LineageSubgraph(
        failing_asset_fqn=failing_fqn,
        nodes=nodes,
        edges=[
            LineageEdge(from_fqn=nodes[0].fqn, to_fqn=nodes[1].fqn)
        ] if len(nodes) >= 2 else [],
        traversal_depth=len(nodes),
    )
    return source_fqn, sg


# ═════════════════════════════════════════════════════════════════════════════
# 1. File Filter
# ═════════════════════════════════════════════════════════════════════════════

class TestIsRelevantYml:

    def test_rejects_github_actions(self):
        assert _is_relevant_yml(".github/workflows/ci.yml", None) is False

    def test_rejects_deploy_dir(self):
        assert _is_relevant_yml("deploy/docker-compose.yml", None) is False

    def test_rejects_docs_dir(self):
        assert _is_relevant_yml("docs/schema.yml", None) is False

    def test_accepts_models_dir(self):
        assert _is_relevant_yml("models/finance/schema.yml", None) is True

    def test_accepts_seeds_dir(self):
        assert _is_relevant_yml("seeds/raw/users.yml", None) is True

    def test_accepts_snapshots_dir(self):
        assert _is_relevant_yml("snapshots/orders.yml", None) is True

    def test_accepts_analyses_dir(self):
        assert _is_relevant_yml("analyses/revenue.yml", None) is True

    def test_accepts_macros_dir(self):
        assert _is_relevant_yml("macros/helpers.yml", None) is True

    def test_ambiguous_path_with_dbt_key_in_patch(self):
        patch = "+version: 2\n+models:\n+  - name: foo\n"
        assert _is_relevant_yml("schema.yml", patch) is True

    def test_ambiguous_path_without_dbt_key_in_patch(self):
        patch = "+some_config: value\n+other: thing\n"
        assert _is_relevant_yml("random.yml", patch) is False

    def test_ambiguous_path_no_patch(self):
        # Conservative reject
        assert _is_relevant_yml("schema.yml", None) is False

    def test_sources_key_triggers_accept(self):
        patch = "+sources:\n+  - name: raw\n"
        assert _is_relevant_yml("config.yml", patch) is True

    def test_nested_models_path(self):
        assert _is_relevant_yml("models/finance/core/schema.yml", None) is True


class TestFilterRelevantFiles:

    def test_sql_always_passes(self):
        assets = [make_asset("models/finance/revenue.sql")]
        result = filter_relevant_files(assets)
        assert len(result) == 1
        assert result[0].filename == "models/finance/revenue.sql"

    def test_ci_yml_filtered_out(self):
        assets = [
            make_asset("models/finance/revenue.sql"),
            make_asset(".github/workflows/ci.yml"),
        ]
        result = filter_relevant_files(assets)
        assert len(result) == 1
        assert result[0].filename == "models/finance/revenue.sql"

    def test_dbt_yml_passes(self):
        assets = [
            make_asset("models/finance/revenue.sql"),
            make_asset("models/finance/schema.yml", YML_PATCH_SINGLE),
        ]
        result = filter_relevant_files(assets)
        assert len(result) == 2

    def test_empty_list(self):
        assert filter_relevant_files([]) == []

    def test_preserves_order(self):
        assets = [
            make_asset("models/a/a.sql"),
            make_asset("models/b/b.sql"),
            make_asset("models/c/c.sql"),
        ]
        result = filter_relevant_files(assets)
        assert [r.filename for r in result] == ["models/a/a.sql", "models/b/b.sql", "models/c/c.sql"]

    def test_python_file_filtered(self):
        assets = [make_asset("dbt_project/utils.py")]
        assert filter_relevant_files(assets) == []

    def test_mixed_extensions(self):
        assets = [
            make_asset("models/a.sql"),
            make_asset("models/b.yaml", YML_PATCH_SINGLE),
            make_asset("README.md"),
            make_asset(".github/ci.yml"),
            make_asset("requirements.txt"),
        ]
        result = filter_relevant_files(assets)
        assert len(result) == 2
        assert {r.filename for r in result} == {"models/a.sql", "models/b.yaml"}


# ═════════════════════════════════════════════════════════════════════════════
# 2. Context Line Stripping
# ═════════════════════════════════════════════════════════════════════════════

class TestStripContextLines:

    def test_removes_context_keeps_changes(self):
        patch = "@@ -1,5 +1,5 @@\n context line\n+added line\n-removed line\n context line\n"
        result = _strip_context_lines(patch)
        assert "+added line" in result
        assert "-removed line" in result
        assert "context line" not in result

    def test_skips_file_headers(self):
        patch = "--- a/models/revenue.sql\n+++ b/models/revenue.sql\n+new line\n"
        result = _strip_context_lines(patch)
        assert "---" not in result
        assert "+++" not in result
        assert "+new line" in result

    def test_none_patch_returns_empty(self):
        assert _strip_context_lines(None) == ""

    def test_empty_patch_returns_empty(self):
        assert _strip_context_lines("") == ""

    def test_only_context_lines_returns_empty(self):
        patch = " context\n context\n context\n"
        assert _strip_context_lines(patch) == ""

    def test_preserves_all_changed_lines(self):
        changed = [f"+line{i}" for i in range(300)]
        patch = "\n".join([" context"] + changed + [" context"])
        result = _strip_context_lines(patch)
        result_lines = result.splitlines()
        assert len(result_lines) == 300

    def test_real_sql_patch(self):
        result = _strip_context_lines(SQL_PATCH_SIMPLE)
        lines = result.splitlines()
        assert any("user_id INT" in l for l in lines)
        assert any("user_id BIGINT" in l for l in lines)
        assert not any(l.startswith(" ") for l in lines)


class TestWarnLargePatch:

    def test_no_warning_under_threshold(self, capsys):
        small_patch = "\n".join([f"+line{i}" for i in range(50)])
        _warn_large_patch("models/finance/revenue.sql", small_patch)
        captured = capsys.readouterr()
        assert "WARNING" not in captured.out

    def test_warning_over_threshold(self, capsys):
        large_patch = "\n".join([f"+line{i}" for i in range(201)])
        _warn_large_patch("models/finance/revenue.sql", large_patch)
        captured = capsys.readouterr()
        assert "WARNING" in captured.out
        assert "201" in captured.out

    def test_exactly_at_threshold_no_warning(self, capsys):
        at_threshold = "\n".join([f"+line{i}" for i in range(200)])
        _warn_large_patch("models/finance/revenue.sql", at_threshold)
        captured = capsys.readouterr()
        assert "WARNING" not in captured.out


# ═════════════════════════════════════════════════════════════════════════════
# 3. SQL FQN Extraction
# ═════════════════════════════════════════════════════════════════════════════

class TestExtractFqnFromSql:

    def test_models_dir(self):
        assert _extract_fqn_from_sql("models/finance/revenue.sql") == "finance.revenue"

    def test_seeds_dir(self):
        assert _extract_fqn_from_sql("seeds/raw/users.sql") == "raw.users"

    def test_snapshots_dir(self):
        assert _extract_fqn_from_sql("snapshots/finance/snap_orders.sql") == "finance.snap_orders"

    def test_analyses_dir(self):
        assert _extract_fqn_from_sql("analyses/revenue_trend.sql") == "revenue_trend"

    def test_nested_models(self):
        assert _extract_fqn_from_sql("models/finance/core/revenue.sql") == "finance.core.revenue"

    def test_no_prefix(self):
        # File not in a known dbt directory
        assert _extract_fqn_from_sql("custom/revenue.sql") == "custom.revenue"

    def test_root_level_file(self):
        assert _extract_fqn_from_sql("revenue.sql") == "revenue"

    def test_macros_dir(self):
        assert _extract_fqn_from_sql("macros/utils/date_spine.sql") == "utils.date_spine"


# ═════════════════════════════════════════════════════════════════════════════
# 4. YML FQN Extraction
# ═════════════════════════════════════════════════════════════════════════════

class TestExtractFqnFromYml:

    def test_single_model_from_patch(self):
        fqns, approximate = _extract_fqn_from_yml("models/finance/schema.yml", YML_PATCH_SINGLE)
        assert approximate is False
        assert fqns == ["finance.orders"]

    def test_multi_model_from_patch(self):
        fqns, approximate = _extract_fqn_from_yml("models/finance/schema.yml", YML_PATCH_MULTI)
        assert approximate is False
        assert "finance.orders" in fqns
        assert "finance.customers" in fqns
        assert "finance.revenue" in fqns
        assert len(fqns) == 3

    def test_source_name_extracted(self):
        fqns, approximate = _extract_fqn_from_yml("models/raw/sources.yml", YML_PATCH_SOURCE)
        assert approximate is False
        assert any("raw_stripe" in fqn for fqn in fqns)

    def test_fallback_to_path_when_no_patch(self):
        fqns, approximate = _extract_fqn_from_yml("models/finance/schema.yml", None)
        assert approximate is True
        assert len(fqns) == 1
        assert fqns[0] == "finance.schema"

    def test_fallback_when_patch_has_no_names(self):
        patch = "+description: some description\n+config:\n+  materialized: table\n"
        fqns, approximate = _extract_fqn_from_yml("models/finance/schema.yml", patch)
        assert approximate is True
        assert len(fqns) == 1

    def test_deduplication_on_rename(self):
        # A rename patch has both - name: old and + name: new
        rename_patch = "-  - name: old_orders\n+  - name: orders\n"
        fqns, approximate = _extract_fqn_from_yml("models/finance/schema.yml", rename_patch)
        assert approximate is False
        # Both old_orders and orders should be present, deduplicated
        assert len(fqns) == 2
        assert "finance.old_orders" in fqns
        assert "finance.orders" in fqns

    def test_yaml_extension(self):
        fqns, approximate = _extract_fqn_from_yml("models/finance/schema.yaml", YML_PATCH_SINGLE)
        assert "finance.orders" in fqns

    def test_domain_from_nested_path(self):
        fqns, approximate = _extract_fqn_from_yml("models/finance/core/schema.yml", YML_PATCH_SINGLE)
        assert "finance.core.orders" in fqns


# ═════════════════════════════════════════════════════════════════════════════
# 5. derive_fqns — composite keys for multi-model ymls
# ═════════════════════════════════════════════════════════════════════════════

class TestDeriveFqns:

    def test_sql_single_entry(self):
        assets = [make_asset("models/finance/revenue.sql", SQL_PATCH_SIMPLE)]
        result = derive_fqns(assets)
        assert "models/finance/revenue.sql" in result
        fqn, approx = result["models/finance/revenue.sql"]
        assert fqn == "finance.revenue"
        assert approx is False

    def test_yml_single_model_uses_filename_key(self):
        assets = [make_asset("models/finance/schema.yml", YML_PATCH_SINGLE)]
        result = derive_fqns(assets)
        assert "models/finance/schema.yml" in result
        fqn, approx = result["models/finance/schema.yml"]
        assert fqn == "finance.orders"
        assert approx is False

    def test_yml_multi_model_uses_composite_key(self):
        assets = [make_asset("models/finance/schema.yml", YML_PATCH_MULTI)]
        result = derive_fqns(assets)
        # Should have 3 entries with composite keys
        assert len(result) == 3
        keys = set(result.keys())
        assert "models/finance/schema.yml::finance.orders" in keys
        assert "models/finance/schema.yml::finance.customers" in keys
        assert "models/finance/schema.yml::finance.revenue" in keys

    def test_mixed_files(self):
        assets = [
            make_asset("models/finance/revenue.sql", SQL_PATCH_SIMPLE),
            make_asset("models/finance/schema.yml", YML_PATCH_MULTI),
        ]
        result = derive_fqns(assets)
        assert len(result) == 4  # 1 sql + 3 from yml

    def test_approximate_flagged_for_fallback(self):
        assets = [make_asset("models/finance/schema.yml", None)]
        result = derive_fqns(assets)
        assert len(result) == 1
        _, approx = list(result.values())[0]
        assert approx is True


# ═════════════════════════════════════════════════════════════════════════════
# 6. Signature Verification
# ═════════════════════════════════════════════════════════════════════════════

class TestVerifyGithubSignature:

    def _make_sig(self, secret: str, payload: bytes) -> str:
        return "sha256=" + hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()

    def test_valid_signature(self):
        payload = b'{"action": "opened"}'
        with patch("controllers.github_controller.GITHUB_WEBHOOK_SECRET", "my-secret"):
            sig = self._make_sig("my-secret", payload)
            assert verify_github_signature(sig, payload) is True

    def test_invalid_signature(self):
        payload = b'{"action": "opened"}'
        with patch("controllers.github_controller.GITHUB_WEBHOOK_SECRET", "my-secret"):
            assert verify_github_signature("sha256=wrong", payload) is False

    def test_missing_secret_returns_true(self):
        with patch("controllers.github_controller.GITHUB_WEBHOOK_SECRET", ""):
            assert verify_github_signature("sha256=anything", b"payload") is True

    def test_tampered_payload_fails(self):
        original = b'{"action": "opened"}'
        tampered = b'{"action": "closed"}'
        with patch("controllers.github_controller.GITHUB_WEBHOOK_SECRET", "my-secret"):
            sig = self._make_sig("my-secret", original)
            assert verify_github_signature(sig, tampered) is False


# ═════════════════════════════════════════════════════════════════════════════
# 7. Webhook URL Builder
# ═════════════════════════════════════════════════════════════════════════════

class TestBuildWebhookUrl:

    def test_builds_correct_url(self):
        url = build_webhook_url("conn-123", "user-456", "https://api.fixflow.io")
        assert url == "https://api.fixflow.io/api/v1/github/webhook?connection_id=conn-123&user_id=user-456"

    def test_strips_trailing_slash(self):
        url = build_webhook_url("conn-123", "user-456", "https://api.fixflow.io/")
        assert not url.endswith("//")
        assert "connection_id=conn-123" in url

    def test_localhost(self):
        url = build_webhook_url("c1", "u1", "http://localhost:8000")
        assert url.startswith("http://localhost:8000/api/v1/github/webhook")


# ═════════════════════════════════════════════════════════════════════════════
# 8. Comment Renderer
# ═════════════════════════════════════════════════════════════════════════════

class TestRenderPlaceholderComment:

    def test_contains_file_list(self):
        assets = [
            make_asset("models/finance/revenue.sql", SQL_PATCH_SIMPLE),
            make_asset("models/finance/schema.yml", YML_PATCH_SINGLE),
        ]
        comment = render_placeholder_comment(assets, "inv-001")
        assert "models/finance/revenue.sql" in comment
        assert "models/finance/schema.yml" in comment

    def test_contains_investigation_id(self):
        assets = [make_asset("models/finance/revenue.sql")]
        comment = render_placeholder_comment(assets, "inv-xyz-123")
        assert "inv-xyz-123" in comment

    def test_file_count(self):
        assets = [make_asset(f"models/t{i}.sql") for i in range(3)]
        comment = render_placeholder_comment(assets, "inv-001")
        assert "3" in comment

    def test_analysis_running_signal(self):
        assets = [make_asset("models/finance/revenue.sql")]
        comment = render_placeholder_comment(assets, "inv-001")
        assert "analysis" in comment.lower() or "running" in comment.lower()


class TestRenderPrComment:

    def setup_method(self):
        self.prc = make_pr_root_cause()
        self.comment = render_pr_comment(self.prc, "inv-001")

    def test_pr_summary_present(self):
        assert "Two schema changes will break 2 downstream assets" in self.comment

    def test_severity_present(self):
        assert "HIGH" in self.comment or "high" in self.comment.lower()

    def test_not_safe_to_merge(self):
        assert "Do NOT merge" in self.comment or "not merge" in self.comment.lower()

    def test_changed_assets_table(self):
        assert "finance.revenue" in self.comment
        assert "finance.orders" in self.comment

    def test_patch_evidence_present(self):
        assert "user_id INT" in self.comment or "user_id BIGINT" in self.comment

    def test_downstream_impact_present(self):
        assert "reporting.daily_revenue" in self.comment
        assert "Daily Revenue Report" in self.comment

    def test_cause_source_asset_present(self):
        assert "finance.revenue" in self.comment
        assert "finance.orders" in self.comment

    def test_error_types_present(self):
        assert "type_mismatch" in self.comment
        assert "missing_column" in self.comment

    def test_file_locations_present(self):
        assert "models/reporting/daily_revenue.sql" in self.comment

    def test_code_snippets_present(self):
        assert "CAST(r.user_id AS BIGINT)" in self.comment

    def test_investigation_id_in_footer(self):
        assert "inv-001" in self.comment

    def test_confidence_in_footer(self):
        assert "88%" in self.comment

    def test_critical_severity_emoji(self):
        assert "🔴" in self.comment

    def test_safe_to_merge_shows_no_breakage(self):
        safe_prc = PRRootCause(
            pr_summary="No impact",
            overall_severity=SeverityLevel.LOW,
            safe_to_merge=True,
            confidence=0.95,
            changed_assets=[],
            downstream_impacts=[],
        )
        comment = render_pr_comment(safe_prc, "inv-safe")
        assert "No Downstream Breakage" in comment or "Safe to merge" in comment


# ═════════════════════════════════════════════════════════════════════════════
# 9. Lineage Merge
# ═════════════════════════════════════════════════════════════════════════════

class TestMergeLineageSubgraphs:

    def test_deduplicates_shared_node(self):
        shared = make_lineage_node("reporting.daily_revenue")
        sg1 = make_subgraph("finance.revenue", [
            make_lineage_node("finance.revenue", is_break_point=True),
            shared,
        ], "finance.revenue")
        shared2 = make_lineage_node("reporting.daily_revenue")
        sg2 = make_subgraph("finance.orders", [
            make_lineage_node("finance.orders", is_break_point=True),
            shared2,
        ], "finance.orders")

        merged = merge_lineage_subgraphs([sg1, sg2])
        fqns = [n.fqn for n in merged.nodes]
        assert fqns.count("reporting.daily_revenue") == 1

    def test_source_assets_annotation(self):
        shared = make_lineage_node("reporting.daily_revenue")
        sg1 = make_subgraph("finance.revenue", [
            make_lineage_node("finance.revenue", is_break_point=True),
            shared,
        ], "finance.revenue")
        shared2 = make_lineage_node("reporting.daily_revenue")
        sg2 = make_subgraph("finance.orders", [
            make_lineage_node("finance.orders", is_break_point=True),
            shared2,
        ], "finance.orders")

        merged = merge_lineage_subgraphs([sg1, sg2])
        downstream = next(n for n in merged.nodes if n.fqn == "reporting.daily_revenue")
        sources = downstream.raw_metadata.get("source_assets", [])
        assert "finance.revenue" in sources
        assert "finance.orders" in sources

    def test_severity_escalation(self):
        low_node  = make_lineage_node("reporting.x", severity=SeverityLevel.LOW)
        high_node = make_lineage_node("reporting.x", severity=SeverityLevel.HIGH)

        sg1 = make_subgraph("a.x", [
            make_lineage_node("a.x", is_break_point=True),
            low_node,
        ], "a.x")
        sg2 = make_subgraph("b.y", [
            make_lineage_node("b.y", is_break_point=True),
            high_node,
        ], "b.y")

        merged = merge_lineage_subgraphs([sg1, sg2])
        node = next(n for n in merged.nodes if n.fqn == "reporting.x")
        assert node.severity == SeverityLevel.HIGH

    def test_edge_deduplication(self):
        node_a = make_lineage_node("a.upstream")
        node_b = make_lineage_node("b.downstream")

        # Both subgraphs contain the same a→b edge
        sg1 = LineageSubgraph(
            failing_asset_fqn="a.upstream",
            nodes=[node_a, node_b],
            edges=[LineageEdge(from_fqn="a.upstream", to_fqn="b.downstream")],
            traversal_depth=2,
        )
        sg2 = LineageSubgraph(
            failing_asset_fqn="a.upstream",
            nodes=[node_a, node_b],
            edges=[LineageEdge(from_fqn="a.upstream", to_fqn="b.downstream")],
            traversal_depth=2,
        )

        merged = merge_lineage_subgraphs([("a.upstream", sg1), ("a.upstream", sg2)])
        assert len(merged.edges) == 1

    def test_max_depth_taken(self):
        sg1 = ("a.x", LineageSubgraph(failing_asset_fqn="a.x", nodes=[], edges=[], traversal_depth=2))
        sg2 = ("b.y", LineageSubgraph(failing_asset_fqn="b.y", nodes=[], edges=[], traversal_depth=3))
        merged = merge_lineage_subgraphs([sg1, sg2])
        assert merged.traversal_depth == 3

    def test_failing_asset_fqn_lists_all_sources(self):
        sg1 = ("finance.revenue", LineageSubgraph(failing_asset_fqn="finance.revenue", nodes=[], edges=[], traversal_depth=1))
        sg2 = ("finance.orders",  LineageSubgraph(failing_asset_fqn="finance.orders",  nodes=[], edges=[], traversal_depth=1))
        merged = merge_lineage_subgraphs([sg1, sg2])
        assert "finance.revenue" in merged.failing_asset_fqn
        assert "finance.orders" in merged.failing_asset_fqn


# ═════════════════════════════════════════════════════════════════════════════
# 10. AI Prompt Builder
# ═════════════════════════════════════════════════════════════════════════════

class TestBuildPrAiContext:

    def setup_method(self):
        self.asset_fqn_map = {
            "models/finance/revenue.sql": ("finance.revenue", False, "-  user_id INT,\n+  user_id BIGINT,"),
            "models/finance/schema.yml::finance.orders": ("finance.orders", False, "-      - name: gross_margin"),
        }
        node = make_lineage_node("reporting.daily_revenue")
        node.raw_metadata["source_assets"] = ["finance.revenue", "finance.orders"]
        self.merged_subgraph = LineageSubgraph(
            failing_asset_fqn="finance.revenue, finance.orders",
            nodes=[node],
            edges=[],
            traversal_depth=1,
        )

    def test_contains_all_changed_files(self):
        ctx = build_pr_ai_context(self.asset_fqn_map, self.merged_subgraph, 42)
        assert "finance.revenue" in ctx
        assert "finance.orders" in ctx

    def test_contains_patch_evidence(self):
        ctx = build_pr_ai_context(self.asset_fqn_map, self.merged_subgraph, 42)
        assert "user_id INT" in ctx
        assert "gross_margin" in ctx

    def test_contains_lineage_with_source_annotation(self):
        ctx = build_pr_ai_context(self.asset_fqn_map, self.merged_subgraph, 42)
        assert "reporting.daily_revenue" in ctx
        assert "reachable from" in ctx

    def test_contains_pr_number(self):
        ctx = build_pr_ai_context(self.asset_fqn_map, self.merged_subgraph, 42)
        assert "42" in ctx

    def test_contains_response_schema_keys(self):
        ctx = build_pr_ai_context(self.asset_fqn_map, self.merged_subgraph, 42)
        for key in ["pr_summary", "overall_severity", "safe_to_merge", "confidence",
                    "changed_assets", "downstream_impacts", "source_asset_fqn",
                    "error_location", "fix", "code_snippet"]:
            assert key in ctx, f"Expected key '{key}' in prompt"

    def test_severity_values_injected(self):
        ctx = build_pr_ai_context(self.asset_fqn_map, self.merged_subgraph, 42)
        for sev in SeverityLevel:
            assert sev.value in ctx

    def test_approximate_fqn_flagged_in_prompt(self):
        asset_fqn_map_approx = {
            "models/finance/schema.yml": ("finance.schema", True, ""),
        }
        ctx = build_pr_ai_context(asset_fqn_map_approx, self.merged_subgraph, 42)
        assert "approximate" in ctx.lower()

    def test_json_only_instruction_present(self):
        ctx = build_pr_ai_context(self.asset_fqn_map, self.merged_subgraph, 42)
        assert "JSON" in ctx
        assert "markdown" in ctx.lower() or "backtick" in ctx.lower()


# ═════════════════════════════════════════════════════════════════════════════
# 11. AI Response Parsing
# ═════════════════════════════════════════════════════════════════════════════

VALID_AI_RESPONSE = {
    "pr_summary": "Two changes will break 1 asset",
    "overall_severity": "high",
    "safe_to_merge": False,
    "confidence": 0.88,
    "changed_assets": [
        {
            "fqn": "finance.revenue",
            "filename": "models/finance/revenue.sql",
            "change_type": "column_type_changed",
            "change_description": "user_id INT → BIGINT",
            "patch_evidence": "-  user_id INT,\n+  user_id BIGINT,",
            "fqn_approximate": False,
        }
    ],
    "downstream_impacts": [
        {
            "fqn": "reporting.daily_revenue",
            "display_name": "Daily Revenue Report",
            "severity": "critical",
            "causes": [
                {
                    "source_asset_fqn": "finance.revenue",
                    "error_type": "type_mismatch",
                    "error_description": "JOIN will fail",
                    "error_location": {
                        "file": "models/reporting/daily_revenue.sql",
                        "clause": "JOIN",
                        "approximate_line": 22,
                    },
                    "fix": {
                        "description": "Add CAST",
                        "fix_type": "add_cast",
                        "target_file": "models/reporting/daily_revenue.sql",
                        "code_snippet": "CAST(r.user_id AS BIGINT)",
                    },
                }
            ],
        }
    ],
}


class TestParseAiResponse:

    def test_parses_valid_response(self):
        result = _parse_pr_ai_response(VALID_AI_RESPONSE)
        assert result is not None
        assert isinstance(result, PRRootCause)

    def test_pr_summary_correct(self):
        result = _parse_pr_ai_response(VALID_AI_RESPONSE)
        assert result.pr_summary == "Two changes will break 1 asset"

    def test_overall_severity_correct(self):
        result = _parse_pr_ai_response(VALID_AI_RESPONSE)
        assert result.overall_severity == SeverityLevel.HIGH

    def test_safe_to_merge_false(self):
        result = _parse_pr_ai_response(VALID_AI_RESPONSE)
        assert result.safe_to_merge is False

    def test_confidence_correct(self):
        result = _parse_pr_ai_response(VALID_AI_RESPONSE)
        assert abs(result.confidence - 0.88) < 0.001

    def test_changed_assets_count(self):
        result = _parse_pr_ai_response(VALID_AI_RESPONSE)
        assert len(result.changed_assets) == 1

    def test_downstream_impacts_count(self):
        result = _parse_pr_ai_response(VALID_AI_RESPONSE)
        assert len(result.downstream_impacts) == 1

    def test_causes_count(self):
        result = _parse_pr_ai_response(VALID_AI_RESPONSE)
        assert len(result.downstream_impacts[0].causes) == 1

    def test_error_location_parsed(self):
        result = _parse_pr_ai_response(VALID_AI_RESPONSE)
        loc = result.downstream_impacts[0].causes[0].error_location
        assert loc.file == "models/reporting/daily_revenue.sql"
        assert loc.clause == "JOIN"
        assert loc.approximate_line == 22

    def test_fix_parsed(self):
        result = _parse_pr_ai_response(VALID_AI_RESPONSE)
        fix = result.downstream_impacts[0].causes[0].fix
        assert fix.fix_type == "add_cast"
        assert "BIGINT" in fix.code_snippet

    def test_missing_required_key_returns_none(self):
        bad = {k: v for k, v in VALID_AI_RESPONSE.items() if k != "pr_summary"}
        result = _parse_pr_ai_response(bad)
        assert result is None

    def test_malformed_cause_skipped_gracefully(self):
        response = {
            **VALID_AI_RESPONSE,
            "downstream_impacts": [
                {
                    "fqn": "reporting.daily_revenue",
                    "display_name": "Daily Revenue",
                    "severity": "critical",
                    "causes": [
                        {"broken": "cause missing required fields"},  # malformed
                        VALID_AI_RESPONSE["downstream_impacts"][0]["causes"][0],  # valid
                    ],
                }
            ],
        }
        result = _parse_pr_ai_response(response)
        assert result is not None
        # Valid cause should still be parsed; malformed skipped
        assert len(result.downstream_impacts[0].causes) == 1

    def test_malformed_changed_asset_skipped_gracefully(self):
        response = {
            **VALID_AI_RESPONSE,
            "changed_assets": [
                {"broken": "missing required fields"},  # malformed
                VALID_AI_RESPONSE["changed_assets"][0],  # valid
            ],
        }
        result = _parse_pr_ai_response(response)
        assert result is not None
        assert len(result.changed_assets) == 1

    def test_empty_downstream_impacts_safe_to_merge(self):
        response = {
            **VALID_AI_RESPONSE,
            "downstream_impacts": [],
            "safe_to_merge": True,
        }
        result = _parse_pr_ai_response(response)
        assert result is not None
        assert result.safe_to_merge is True
        assert result.downstream_impacts == []


# ═════════════════════════════════════════════════════════════════════════════
# 12. Investigation Creation — event_type param
# ═════════════════════════════════════════════════════════════════════════════

class TestCreateInvestigation:

    @patch("controllers.investigation_controller.investigations_collection")
    @patch("controllers.investigation_controller.event_controller")
    def test_manual_event_type_default(self, mock_event_ctrl, mock_collection):
        mock_collection.insert_one.return_value = MagicMock(inserted_id="507f1f77bcf86cd799439011")
        mock_event_ctrl.mark_event_processed.return_value = True

        from controllers.investigation_controller import create_investigation
        create_investigation(
            user_id="user-1",
            connection_id="conn-1",
            event_id="event-1",
            failure_message="test failure",
        )

        inserted_doc = mock_collection.insert_one.call_args[0][0]
        assert inserted_doc["event_type"] == "manual"

    @patch("controllers.investigation_controller.investigations_collection")
    @patch("controllers.investigation_controller.event_controller")
    def test_github_pr_event_type(self, mock_event_ctrl, mock_collection):
        mock_collection.insert_one.return_value = MagicMock(inserted_id="507f1f77bcf86cd799439011")
        mock_event_ctrl.mark_event_processed.return_value = True

        from controllers.investigation_controller import create_investigation
        create_investigation(
            user_id="user-1",
            connection_id="conn-1",
            event_id="github-pr-42",
            failure_message="PR #42",
            event_type="github_pr",
        )

        inserted_doc = mock_collection.insert_one.call_args[0][0]
        assert inserted_doc["event_type"] == "github_pr"

    @patch("controllers.investigation_controller.investigations_collection")
    @patch("controllers.investigation_controller.event_controller")
    def test_pr_root_cause_field_initialised_as_none(self, mock_event_ctrl, mock_collection):
        mock_collection.insert_one.return_value = MagicMock(inserted_id="507f1f77bcf86cd799439011")
        mock_event_ctrl.mark_event_processed.return_value = True

        from controllers.investigation_controller import create_investigation
        create_investigation("u", "c", "e", "msg", event_type="github_pr")

        inserted_doc = mock_collection.insert_one.call_args[0][0]
        assert "pr_root_cause" in inserted_doc
        assert inserted_doc["pr_root_cause"] is None


# ═════════════════════════════════════════════════════════════════════════════
# 13. PR Root Cause Deserialisation
# ═════════════════════════════════════════════════════════════════════════════

class TestDeserialisePrRootCause:

    def _raw(self):
        prc = make_pr_root_cause()
        return prc.model_dump()

    def test_round_trips_correctly(self):
        raw = self._raw()
        result = _deserialise_pr_root_cause(raw)
        assert result is not None
        assert result.pr_summary == "Two schema changes will break 2 downstream assets"

    def test_downstream_impacts_count(self):
        result = _deserialise_pr_root_cause(self._raw())
        assert len(result.downstream_impacts) == 1

    def test_causes_count(self):
        result = _deserialise_pr_root_cause(self._raw())
        assert len(result.downstream_impacts[0].causes) == 2

    def test_confidence_preserved(self):
        result = _deserialise_pr_root_cause(self._raw())
        assert abs(result.confidence - 0.88) < 0.001

    def test_severity_preserved(self):
        result = _deserialise_pr_root_cause(self._raw())
        assert result.overall_severity == SeverityLevel.HIGH

    def test_malformed_cause_skipped(self):
        raw = self._raw()
        raw["downstream_impacts"][0]["causes"].append({"broken": True})
        result = _deserialise_pr_root_cause(raw)
        assert result is not None
        # Still parses the 2 valid causes, skips the broken one
        assert len(result.downstream_impacts[0].causes) == 2

    def test_error_location_fields_preserved(self):
        result = _deserialise_pr_root_cause(self._raw())
        loc = result.downstream_impacts[0].causes[0].error_location
        assert loc.clause == "JOIN"
        assert loc.approximate_line == 22

    def test_fix_code_snippet_preserved(self):
        result = _deserialise_pr_root_cause(self._raw())
        fix = result.downstream_impacts[0].causes[0].fix
        assert "BIGINT" in fix.code_snippet


# ═════════════════════════════════════════════════════════════════════════════
# 14. Webhook Handler (FastAPI route)
# ═════════════════════════════════════════════════════════════════════════════

class TestWebhookHandler:
    """
    Tests the FastAPI webhook endpoint using TestClient with mocked dependencies.
    """

    def _make_client(self):
        from fastapi import FastAPI
        from routes.github import router
        app = FastAPI()
        app.include_router(router, prefix="/api/v1")
        from fastapi.testclient import TestClient
        return TestClient(app, raise_server_exceptions=False)

    def _make_payload(self, pr_number: int = 42) -> dict:
        return {
            "action": "opened",
            "installation": {"id": 123456},
            "repository": {
                "name": "data-warehouse",
                "full_name": "acme/data-warehouse",
                "owner": {"login": "acme", "id": 1},
            },
            "pull_request": {
                "number": pr_number,
                "title": "Add new column",
                "html_url": f"https://github.com/acme/data-warehouse/pull/{pr_number}",
                "user": {"login": "dev", "id": 2},
                "base": {"ref": "main"},
                "head": {"ref": "feature/new-col"},
            },
            "sender": {"login": "dev", "id": 2},
        }

    def _sign(self, payload: bytes, secret: str = "test-secret") -> str:
        return "sha256=" + hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()

    def test_missing_connection_id_returns_400(self):
        client = self._make_client()
        resp = client.post(
            "/api/v1/github/webhook?user_id=user-1",
            content=b"{}",
            headers={"X-Hub-Signature-256": "sha256=abc", "X-GitHub-Event": "pull_request"},
        )
        assert resp.status_code == 400

    def test_missing_signature_returns_401(self):
        client = self._make_client()
        resp = client.post(
            "/api/v1/github/webhook?connection_id=conn-1&user_id=user-1",
            content=b"{}",
            headers={"X-GitHub-Event": "pull_request"},
        )
        assert resp.status_code == 401

    def test_invalid_signature_returns_401(self):
        client = self._make_client()
        with patch("controllers.github_controller.GITHUB_WEBHOOK_SECRET", "test-secret"):
            resp = client.post(
                "/api/v1/github/webhook?connection_id=conn-1&user_id=user-1",
                content=b'{"action":"opened"}',
                headers={
                    "X-Hub-Signature-256": "sha256=bad",
                    "X-GitHub-Event": "pull_request",
                },
            )
        assert resp.status_code == 401

    def test_non_pr_event_ignored(self):
        client = self._make_client()
        payload = b'{"action":"created"}'
        sig = self._sign(payload)
        with patch("controllers.github_controller.GITHUB_WEBHOOK_SECRET", "test-secret"):
            resp = client.post(
                "/api/v1/github/webhook?connection_id=conn-1&user_id=user-1",
                content=payload,
                headers={"X-Hub-Signature-256": sig, "X-GitHub-Event": "push"},
            )
        assert resp.status_code in (200, 202)
        data = resp.json()
        assert data.get("analyzed") is not True

    @patch("controllers.github_controller.GITHUB_WEBHOOK_SECRET", "test-secret")
    @patch("routes.github.connection_controller")
    @patch("routes.github.github_controller")
    @patch("routes.github.investigation_controller")
    def test_no_relevant_files_returns_not_analyzed(
        self, mock_inv_ctrl, mock_gh_ctrl, mock_conn_ctrl
    ):
        client = self._make_client()
        payload = json.dumps(self._make_payload()).encode()
        sig = self._sign(payload)

        mock_conn_ctrl.get_connection_by_id.return_value = MagicMock(
            user_id="user-1",
            openmetadata_host="http://om.local",
            openmetadata_token="token",
            github_installation_id="123456",
        )
        mock_gh_ctrl.verify_github_signature.return_value = True
        mock_gh_ctrl.get_installation_token.return_value = "gh-token"
        mock_gh_ctrl.parse_pr_diff.return_value = [
            ChangedAsset(filename="README.md", status="modified", additions=1, deletions=0, changes=1)
        ]
        mock_gh_ctrl.filter_relevant_files.return_value = []

        resp = client.post(
            "/api/v1/github/webhook?connection_id=conn-1&user_id=user-1",
            content=payload,
            headers={"X-Hub-Signature-256": sig, "X-GitHub-Event": "pull_request"},
        )
        assert resp.status_code == 202
        assert resp.json()["analyzed"] is False

    @patch("controllers.github_controller.GITHUB_WEBHOOK_SECRET", "test-secret")
    @patch("routes.github.connection_controller")
    @patch("routes.github.github_controller")
    @patch("routes.github.investigation_controller")
    def test_relevant_files_triggers_investigation(
        self, mock_inv_ctrl, mock_gh_ctrl, mock_conn_ctrl
    ):
        client = self._make_client()
        payload = json.dumps(self._make_payload()).encode()
        sig = self._sign(payload)

        relevant = [
            ChangedAsset(filename="models/finance/revenue.sql", status="modified",
                         additions=2, deletions=1, changes=3, patch=SQL_PATCH_SIMPLE),
        ]
        mock_conn_ctrl.get_connection_by_id.return_value = MagicMock(
            user_id="user-1",
            openmetadata_host="http://om.local",
            openmetadata_token="token",
            github_installation_id="123456",
        )
        mock_gh_ctrl.verify_github_signature.return_value = True
        mock_gh_ctrl.get_installation_token.return_value = "gh-token"
        mock_gh_ctrl.parse_pr_diff.return_value = relevant
        mock_gh_ctrl.filter_relevant_files.return_value = relevant
        mock_gh_ctrl.derive_fqns.return_value = {
            "models/finance/revenue.sql": ("finance.revenue", False)
        }
        mock_gh_ctrl._strip_context_lines.return_value = "-  user_id INT,\n+  user_id BIGINT,"
        mock_gh_ctrl.render_placeholder_comment.return_value = "Analysis started..."
        mock_gh_ctrl.post_pr_comment.return_value = "comment-123"
        mock_inv_ctrl.create_investigation.return_value = "inv-001"

        resp = client.post(
            "/api/v1/github/webhook?connection_id=conn-1&user_id=user-1",
            content=payload,
            headers={"X-Hub-Signature-256": sig, "X-GitHub-Event": "pull_request"},
        )
        assert resp.status_code == 202
        data = resp.json()
        assert data["analyzed"] is True
        assert data["investigation_id"] == "inv-001"
        assert data["comment_id"] == "comment-123"

    @patch("controllers.github_controller.GITHUB_WEBHOOK_SECRET", "test-secret")
    @patch("routes.github.connection_controller")
    @patch("routes.github.github_controller")
    @patch("routes.github.investigation_controller")
    def test_multi_model_yml_all_composite_keys_reach_investigation(
        self, mock_inv_ctrl, mock_gh_ctrl, mock_conn_ctrl
    ):
        """
        Regression test for Finding 2: multi-model yml composite keys silently dropped.

        Before the fix, the asset_fqn_map build loop iterated relevant_files and
        checked `asset.filename in raw_fqn_map`. For a multi-model yml,
        raw_fqn_map keys are composite ("models/finance/schema.yml::finance.orders")
        so `asset.filename` ("models/finance/schema.yml") never matched — all three
        models were dropped and run_pr_investigation received an empty map.

        After the fix, the loop iterates raw_fqn_map keys directly and splits on "::"
        to recover the base filename for patch lookup. All 3 composite entries must
        appear in the asset_fqns list returned in the response.
        """
        client = self._make_client()
        payload = json.dumps(self._make_payload()).encode()
        sig = self._sign(payload)

        yml_asset = ChangedAsset(
            filename="models/finance/schema.yml",
            status="modified",
            additions=3,
            deletions=1,
            changes=4,
            patch=YML_PATCH_MULTI,
        )

        mock_conn_ctrl.get_connection_by_id.return_value = MagicMock(
            user_id="user-1",
            openmetadata_host="http://om.local",
            openmetadata_token="token",
            github_installation_id="123456",
        )
        mock_gh_ctrl.verify_github_signature.return_value = True
        mock_gh_ctrl.get_installation_token.return_value = "gh-token"
        mock_gh_ctrl.parse_pr_diff.return_value = [yml_asset]
        mock_gh_ctrl.filter_relevant_files.return_value = [yml_asset]
        # derive_fqns returns composite keys — exact shape the real controller produces
        mock_gh_ctrl.derive_fqns.return_value = {
            "models/finance/schema.yml::finance.orders":    ("finance.orders",    False),
            "models/finance/schema.yml::finance.customers": ("finance.customers", False),
            "models/finance/schema.yml::finance.revenue":   ("finance.revenue",   False),
        }
        mock_gh_ctrl._strip_context_lines.return_value = "-  - name: id\n+  - name: customer_id"
        mock_gh_ctrl.render_placeholder_comment.return_value = "Analysis started..."
        mock_gh_ctrl.post_pr_comment.return_value = "comment-456"
        mock_inv_ctrl.create_investigation.return_value = "inv-002"

        resp = client.post(
            "/api/v1/github/webhook?connection_id=conn-1&user_id=user-1",
            content=payload,
            headers={"X-Hub-Signature-256": sig, "X-GitHub-Event": "pull_request"},
        )

        assert resp.status_code == 202
        data = resp.json()
        assert data["analyzed"] is True
        assert data["investigation_id"] == "inv-002"

        # Core regression assertion: all 3 FQNs must survive the asset_fqn_map
        # build step and appear in the response. Pre-fix this would be 0.
        asset_fqns = data["asset_fqns"]
        assert len(asset_fqns) == 3, (
            f"Expected 3 FQN entries for multi-model yml, got {len(asset_fqns)}. "
            "Multi-model yml composite keys are being silently dropped before "
            "run_pr_investigation — check the asset_fqn_map build loop in "
            "routes/github.py (github_pr_webhook, Step 3)."
        )
        assert "finance.orders" in asset_fqns
        assert "finance.customers" in asset_fqns
        assert "finance.revenue" in asset_fqns

        # Secondary assertion: _strip_context_lines called once per composite key,
        # confirming each model's patch was individually looked up, not skipped.
        assert mock_gh_ctrl._strip_context_lines.call_count == 3

# ═════════════════════════════════════════════════════════════════════════════
# 15. Integration — full PR bot pipeline with mocked external calls
# ═════════════════════════════════════════════════════════════════════════════

class TestIntegrationPrBotPipeline:
    """
    Tests run_pr_investigation end-to-end with all external calls mocked.
    Verifies the correct sequence of operations and final state.
    """

    @patch("controllers.github_controller.update_pr_comment")
    @patch("controllers.github_controller.render_pr_comment")
    @patch("controllers.investigation_controller.call_pr_ai_layer")
    @patch("controllers.investigation_controller.lineage_controller")
    @patch("controllers.investigation_controller.investigations_collection")
    def test_full_pipeline_happy_path(
        self,
        mock_collection,
        mock_lineage_ctrl,
        mock_ai_layer,
        mock_renderer,
        mock_update_comment,
    ):
        from controllers.investigation_controller import run_pr_investigation

        # Mock lineage traversal
        nodes = [
            make_lineage_node("finance.revenue", is_break_point=True),
            make_lineage_node("reporting.daily_revenue"),
        ]
        mock_lineage_ctrl.traverse_upstream.return_value = nodes
        mock_lineage_ctrl.detect_break_point.return_value = nodes

        # Mock AI result
        mock_ai_layer.return_value = make_pr_root_cause()

        # Mock renderer
        mock_renderer.return_value = "## Full analysis comment"

        # Mock DB
        mock_collection.find_one.return_value = {"_id": "inv-001", "status": "pending"}
        mock_collection.update_one.return_value = MagicMock(modified_count=1)

        asset_fqn_map = {
            "models/finance/revenue.sql": ("finance.revenue", False, "-  user_id INT,\n+  user_id BIGINT,"),
        }

        result = run_pr_investigation(
            investigation_id="507f1f77bcf86cd799439011",
            user_id="user-1",
            connection_id="conn-1",
            openmetadata_url="http://om.local",
            openmetadata_token="token",
            asset_fqn_map=asset_fqn_map,
            pr_number=42,
            gh_token="gh-token",
            repo_owner="acme",
            repo_name="data-warehouse",
            comment_id="comment-123",
        )

        assert result is True
        mock_lineage_ctrl.traverse_upstream.assert_called_once()
        mock_ai_layer.assert_called_once()
        mock_renderer.assert_called_once()
        mock_update_comment.assert_called_once()

    @patch("controllers.investigation_controller.update_investigation_status")
    @patch("controllers.investigation_controller.lineage_controller")
    @patch("controllers.investigation_controller.investigations_collection")
    def test_no_lineage_returns_false(
        self, mock_collection, mock_lineage_ctrl, mock_status
    ):
        from controllers.investigation_controller import run_pr_investigation

        mock_lineage_ctrl.traverse_upstream.return_value = []

        result = run_pr_investigation(
            investigation_id="507f1f77bcf86cd799439011",
            user_id="user-1",
            connection_id="conn-1",
            openmetadata_url="http://om.local",
            openmetadata_token="token",
            asset_fqn_map={"models/finance/revenue.sql": ("finance.revenue", False, "")},
            pr_number=42,
            gh_token="gh-token",
            repo_owner="acme",
            repo_name="data-warehouse",
            comment_id="comment-123",
        )

        assert result is False

    @patch("controllers.investigation_controller.update_investigation_status")
    @patch("controllers.investigation_controller.call_pr_ai_layer")
    @patch("controllers.investigation_controller.lineage_controller")
    @patch("controllers.investigation_controller.investigations_collection")
    def test_ai_failure_returns_false(
        self, mock_collection, mock_lineage_ctrl, mock_ai_layer, mock_status
    ):
        from controllers.investigation_controller import run_pr_investigation

        nodes = [make_lineage_node("finance.revenue", is_break_point=True)]
        mock_lineage_ctrl.traverse_upstream.return_value = nodes
        mock_lineage_ctrl.detect_break_point.return_value = nodes
        mock_ai_layer.return_value = None   # AI fails
        mock_collection.update_one.return_value = MagicMock(modified_count=1)

        result = run_pr_investigation(
            investigation_id="507f1f77bcf86cd799439011",
            user_id="user-1",
            connection_id="conn-1",
            openmetadata_url="http://om.local",
            openmetadata_token="token",
            asset_fqn_map={"models/finance/revenue.sql": ("finance.revenue", False, "")},
            pr_number=42,
            gh_token="gh-token",
            repo_owner="acme",
            repo_name="data-warehouse",
            comment_id="comment-123",
        )

        assert result is False

    @patch("controllers.github_controller.update_pr_comment")
    @patch("controllers.github_controller.render_pr_comment")
    @patch("controllers.investigation_controller.call_pr_ai_layer")
    @patch("controllers.investigation_controller.lineage_controller")
    @patch("controllers.investigation_controller.investigations_collection")
    def test_multi_asset_traversal_called_per_fqn(
        self, mock_collection, mock_lineage_ctrl, mock_ai_layer, mock_renderer, mock_update
    ):
        from controllers.investigation_controller import run_pr_investigation

        nodes = [make_lineage_node("reporting.x")]
        mock_lineage_ctrl.traverse_upstream.return_value = nodes
        mock_lineage_ctrl.detect_break_point.return_value = nodes
        mock_ai_layer.return_value = make_pr_root_cause()
        mock_renderer.return_value = "comment"
        mock_collection.update_one.return_value = MagicMock(modified_count=1)

        asset_fqn_map = {
            "models/finance/revenue.sql":                  ("finance.revenue", False, ""),
            "models/finance/schema.yml::finance.orders":   ("finance.orders",  False, ""),
            "models/finance/schema.yml::finance.customers":("finance.customers",False, ""),
        }

        run_pr_investigation(
            investigation_id="507f1f77bcf86cd799439011",
            user_id="user-1",
            connection_id="conn-1",
            openmetadata_url="http://om.local",
            openmetadata_token="token",
            asset_fqn_map=asset_fqn_map,
            pr_number=42,
            gh_token="gh-token",
            repo_owner="acme",
            repo_name="data-warehouse",
            comment_id="comment-123",
        )

        # traverse_upstream should be called once per FQN
        assert mock_lineage_ctrl.traverse_upstream.call_count == 3