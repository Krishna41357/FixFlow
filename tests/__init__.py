"""
tests/ — Comprehensive test suite for Pipeline Autopsy

Directory structure:
- test_auth_controller.py        — Authentication & password handling tests
- test_lineage_controller.py     — Lineage traversal & break point detection
- test_investigation_controller.py — Investigation pipeline tests
- test_event_controller.py       — Event intake (dbt/GitHub/manual) tests
- test_other_controllers.py      — Connection, GitHub, Chat controller tests
- conftest.py                    — Shared fixtures and pytest configuration
- pytest.ini                     — Pytest configuration

Quick Start:

    # Run all tests
    pytest tests/ -v
    
    # Run specific test file
    pytest tests/test_auth_controller.py -v
    
    # Run specific test
    pytest tests/test_auth_controller.py::TestAuthPasswordHandling::test_verify_password_correct -v
    
    # Run with coverage
    pytest tests/ --cov=controllers --cov=models --cov-report=html
    
    # Run tests by marker
    pytest -m auth      # Run only auth tests
    pytest -m webhook   # Run only webhook tests
    
    # Run tests in parallel (requires pytest-xdist)
    pytest tests/ -n 4  # Run with 4 workers

Test Organization by Controller:

    ✅ auth_controller.py (9 functions)
       - test_get_password_hash_generates_hash
       - test_verify_password_correct
       - test_create_access_token_success
       - test_verify_token_valid
       - test_register_user_success
       - test_login_user_success
       - test_get_user_by_id_success
       - test_get_user_by_email_success
       + 25 edge case tests

    ✅ lineage_controller.py (2 functions)
       - test_traverse_upstream_success
       - test_detect_break_point_column_renamed
       - test_detect_break_point_column_dropped
       - test_detect_break_point_type_change
       + 6 edge case tests

    ✅ investigation_controller.py (5 functions)
       - test_create_investigation_success
       - test_run_investigation_success
       - test_build_ai_context_success
       - test_call_ai_layer_claude_success
       - test_update_investigation_status_pending
       + 10 edge case tests

    ✅ event_controller.py (4 functions)
       - test_handle_dbt_webhook_success
       - test_handle_github_pr_valid_signature
       - test_handle_manual_query_success
       - test_get_events_for_user_success
       + 6 edge case tests

    ✅ Other Controllers (connection, github, chat - 15 functions)
       - test_create_connection_success
       - test_verify_openmetadata_connection
       - test_verify_github_signature_valid
       - test_parse_pr_diff_sql_files
       - test_create_session_success
       - test_handle_query_new_investigation
       + 20 edge case tests

Total: 70+ comprehensive tests covering:
- Happy path scenarios
- Error conditions
- Edge cases
- Integration between components

Coverage target: >85% for critical components
"""
