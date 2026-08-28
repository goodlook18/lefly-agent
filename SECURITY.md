# Security Policy

## Local-development boundary

LeFly Agent `v0.1.1` provides local Simulator, Console, Agent Control, and
Device Protocol services for development. They do not provide production
authentication, authorization, tenant isolation, rate limiting, or TLS.

Bind services to loopback and do not expose ports `8766` or `8767` to an
untrusted network. A connected endpoint may execute robot motion or lighting
commands. The browser control lease coordinates local operators but is not a
security credential.

Keep model, weather, and search credentials in environment variables. Do not
put secrets in TOML, screenshots, logs, issues, or test fixtures.

## Supported versions

Security fixes are provided for the latest released minor version.

## Reporting a vulnerability

Use the repository host's private security-advisory feature. Do not publish
credentials, personal data, device identifiers, network configuration, or an
unpatched exploit in a public issue.

Include the affected version, reproduction steps, impact, and any suggested
mitigation. Maintainers will acknowledge a complete report within seven days.
