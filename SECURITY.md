# Security Policy

## Supported versions

This is a single-maintainer, `main`-tracked project. Only the latest `main` is
supported; fixes are not backported to tags.

## Reporting a vulnerability

**Do not open a public issue for a security problem.**

Use GitHub's private reporting instead — open the repository's **Security** tab,
click **Report a vulnerability**, and submit a draft advisory. That channel is
private between you and the maintainer until a fix ships.

If you cannot use GitHub advisories, open a public issue that contains **no
details** and asks for a private contact channel.

Please include:

- a description of the issue and its impact,
- the affected file, route, or component,
- minimal steps to reproduce (a PoC if you have one),
- the commit/version you tested.

Do not include live credentials in a report. If you have found a committed
secret, say which file and commit — do not paste the value.

## What is in scope

- The library under `src/knowledge_engine/` and the HTTP service in
  `src/knowledge_engine/service/`.
- The CI/CD and agent configuration in `.github/` and `.opencode/`.
- Committed secrets (see the secret-handling policy below).

Out of scope: issues in third-party dependencies (report those upstream), and
anything requiring a compromised host or physical access.

## Secret-handling policy

Credentials are read from the environment only and are never committed
(`.env` is git-ignored; `.env.example` holds placeholders). This is enforced by:

- a `detect-secrets` pre-commit hook (`.secrets.baseline` is an audited
  allowlist of known false positives), and
- a `gitleaks` scan of the full git history in CI (`.gitleaks.toml`).

A committed credential is treated as a confirmed incident: the secret should be
rotated immediately, not merely deleted, because it remains in history.

## Response expectations

This is a volunteer, zero-budget project. There is no bug-bounty program. Best
effort: acknowledgement within a few days and a fix or mitigation plan as soon
as practical. Coordinated disclosure is appreciated — please give time for a
fix before publishing.
