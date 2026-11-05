# Hyundai Bluelink JP contributor instructions

Hyundai Bluelink JP is an independent Japan-only Home Assistant integration. The API is
bundled in `custom_components/hyundai_jp/_api`; there is no separately released
API package and no dependency on other regional adapters.

- Preserve the full working Japan behavior, including command state watching,
  refresh, SVM cameras, profile-aware controls and redacted diagnostics.
- Keep vehicle communication in `_api` and Home Assistant mapping in its parent.
- Never send live vehicle commands or use account credentials without explicit
  permission. Tests and GitHub workflows must be offline with sockets blocked.
- Do not commit personal credentials, diagnostic captures or private files.
- Missing vehicle data stays unknown; do not invent successful command outcomes.
- Use atomic conventional commits. Preserve original MIT licenses and attribution.
- Keep tests for Japan, its shared helpers, Home Assistant and release packaging.
  Remove tests and fixtures that exist only for discarded regions.
- Run meaningful offline regression tests, strict typing and formatting checks.
- Package the component contents at the ZIP root for HACS `zip_release`.
- Keep versioned releases immutable and retain older releases for rollback.
- Existing upstream forks, branches, PRs and old build ZIPs are historical
  references; do not modify or delete them for this migration.
