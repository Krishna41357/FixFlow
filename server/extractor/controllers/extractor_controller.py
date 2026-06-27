"""
extractor_controller.py — Public API for the extractor module.

This is the only file other modules should import from. Everything
else in extractor/ (classifiers, registry, models) is internal —
mirrors how repo_parser_controller.py exposes scan_repo/get_repo_graph
as its public surface while keeping parsing internals private (_-prefixed).

Public functions:
  classify_repo   — full repo scan: fetch tree, detect stack, classify all files
  classify_changed_files — scoped version for PR-time use (changed files only),
                            reuses an already-detected StackProfile so PR-time
                            classification doesn't redetect the stack on every push
  extract_repo    — Stage 3: runs identity extraction over every classified,
                     extractable file from a RepoClassification, returns one
                     ExtractionResult per file

Network/file-tree fetching pattern is copied from repo_parser_controller.py's
_get_repo_file_tree — same endpoint, same headers, same graceful-empty-list
behavior on failure. Kept as a local function here since extractor/ is
designed to be a standalone module (per project design decision), not
importing from server/controllers.
"""

from typing import List, Optional

import requests

from extractor.models.classification import RepoClassification, StackProfile, ClassifiedFile
from extractor.models.identity import ExtractionResult
from extractor.classifiers.stack_detector import detect_stack, _fetch_root_file
from extractor.classifiers.rule_engine import classify_files
from extractor.registry.stack_registry import get_rule_set
from extractor.extractors.registry import get_extractor

GITHUB_API_TIMEOUT = 15


def _get_repo_file_tree(
    github_token: str,
    repo_owner: str,
    repo_name: str,
) -> List[str]:
    """
    Fetches the full recursive file path list for the default branch.
    Mirrors repo_parser_controller.py's _get_repo_file_tree, returning
    just the path strings since that's all the classifier needs.
    """
    try:
        url = f"https://api.github.com/repos/{repo_owner}/{repo_name}/git/trees/HEAD"
        headers = {
            "Authorization": f"token {github_token}",
            "Accept": "application/vnd.github.v3+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        params = {"recursive": "1"}

        resp = requests.get(url, headers=headers, params=params, timeout=GITHUB_API_TIMEOUT)

        if resp.status_code == 200:
            data = resp.json()
            paths = [
                entry["path"] for entry in data.get("tree", [])
                if entry.get("type") == "blob"
            ]
            print(f"DEBUG _get_repo_file_tree: Found {len(paths)} files in {repo_owner}/{repo_name}")
            return paths
        else:
            print(f"ERROR _get_repo_file_tree: Status {resp.status_code} — {resp.text[:200]}")
            return []

    except Exception as e:
        print(f"ERROR _get_repo_file_tree: {e}")
        return []


def classify_repo(
    github_token: str,
    repo_owner: str,
    repo_name: str,
    stack_profile: Optional[StackProfile] = None,
) -> RepoClassification:
    """
    Full repo classification — the extractor module's main entrypoint.

    Steps:
      1. Detect stack (skipped if stack_profile already provided —
         callers building the persistent graph at onboarding will detect
         once and reuse; PR-time callers should pass the cached profile)
      2. Fetch full file tree
      3. Resolve the matching rule set from the registry
      4. Classify every file (content fetched lazily only where rules need it)

    Returns RepoClassification with every file tagged — including UNKNOWN
    for anything no rule recognized. UNKNOWN is a legitimate result, not
    an error; callers decide what to do with low-confidence/unknown files.
    """
    repo_full_name = f"{repo_owner}/{repo_name}"

    if stack_profile is None:
        stack_profile = detect_stack(github_token, repo_owner, repo_name)

    if not stack_profile.is_recognized:
        print(
            f"WARNING classify_repo: stack not recognized for {repo_full_name} — "
            f"falling back to universal rules only, most files will be UNKNOWN"
        )

    file_paths = _get_repo_file_tree(github_token, repo_owner, repo_name)
    if not file_paths:
        print(f"WARNING classify_repo: empty file tree for {repo_full_name}")
        return RepoClassification(
            repo_full_name=repo_full_name,
            stack_profile=stack_profile,
            files=[],
            total_files_scanned=0,
        )

    rule_set = get_rule_set(stack_profile)

    def _content_fetcher(path: str) -> Optional[str]:
        return _fetch_root_file(github_token, repo_owner, repo_name, path)

    classified = classify_files(file_paths, rule_set, content_fetcher=_content_fetcher)

    result = RepoClassification(
        repo_full_name=repo_full_name,
        stack_profile=stack_profile,
        files=classified,
        total_files_scanned=len(file_paths),
    )

    print(
        f"DEBUG classify_repo: {repo_full_name} — {len(classified)} files classified, "
        f"tag breakdown: {result.tag_counts}"
    )
    return result


def classify_changed_files(
    github_token: str,
    repo_owner: str,
    repo_name: str,
    changed_paths: List[str],
    stack_profile: StackProfile,
) -> RepoClassification:
    """
    Scoped classification for PR-time use — classifies only the files
    that changed in a PR, reusing an already-known StackProfile rather
    than re-detecting it on every push (stack rarely changes mid-PR;
    re-detecting per push would be wasted API calls).

    Same rule engine, same content-fetch behavior — just a smaller
    input list. This is the function the eventual PR webhook flow
    should call once Stage 3/4 (identity + edges) exist downstream.
    """
    repo_full_name = f"{repo_owner}/{repo_name}"
    rule_set = get_rule_set(stack_profile)

    def _content_fetcher(path: str) -> Optional[str]:
        return _fetch_root_file(github_token, repo_owner, repo_name, path)

    classified = classify_files(changed_paths, rule_set, content_fetcher=_content_fetcher)

    return RepoClassification(
        repo_full_name=repo_full_name,
        stack_profile=stack_profile,
        files=classified,
        total_files_scanned=len(changed_paths),
    )


def extract_repo(
    github_token: str,
    repo_owner: str,
    repo_name: str,
    classification: RepoClassification,
) -> List[ExtractionResult]:
    """
    Stage 3 entrypoint — runs identity extraction over every extractable
    file from a RepoClassification (the output of classify_repo()).

    Flow, per extractable file:
      1. Look up which extractor handles file.pending_extractor via the
         extractors registry
      2. If no extractor is registered for that hint yet, skip the file
         (not an error — incremental rollout means most hints won't
         have an extractor implemented for a while)
      3. Fetch the file's content
      4. Call extractor.extract(path, content, classified_file)
      5. Collect every ExtractionResult, even ones with parse_errors —
         callers decide what to do with partial/failed extractions

    Returns one ExtractionResult per file that had a registered
    extractor and fetchable content. Files with no extractor or no
    fetchable content are silently skipped, not represented in the
    output list at all — this keeps the return type simple (a flat
    list of genuine results) rather than mixing in "skipped" markers
    that every caller would need to filter out anyway.
    """
    results: List[ExtractionResult] = []
    skipped_no_extractor = 0
    skipped_no_content = 0

    for classified_file in classification.extractable_files:
        extractor_fn = get_extractor(classified_file.pending_extractor or "")
        if extractor_fn is None:
            skipped_no_extractor += 1
            continue

        content = _fetch_root_file(github_token, repo_owner, repo_name, classified_file.path)
        if content is None:
            skipped_no_content += 1
            continue

        result = extractor_fn(classified_file.path, content, classified_file)
        results.append(result)

    total_identities = sum(r.identity_count for r in results)
    total_references = sum(len(r.references) for r in results)
    failed_count = sum(1 for r in results if r.had_errors)

    print(
        f"DEBUG extract_repo: {classification.repo_full_name} — "
        f"{len(results)} files extracted ({total_identities} identities, "
        f"{total_references} references, {failed_count} with parse errors), "
        f"{skipped_no_extractor} skipped (no extractor registered), "
        f"{skipped_no_content} skipped (content unfetchable)"
    )

    return results
