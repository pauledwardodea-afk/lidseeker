# Security Policy

Lidseeker is **beta** software. It's a self-hosted app that holds your Lidarr API key and user
credentials, so please treat security reports seriously.

## Supported versions

Only the latest release / `master` is supported. Fixes land there; there are no back-ports.

## Reporting a vulnerability

Please **do not** open a public issue for security-sensitive problems.

Use GitHub's **private vulnerability reporting**: go to the repo's **Security** tab →
**Report a vulnerability**. That opens a private advisory visible only to the maintainers.

Include what you found, how to reproduce it, and the impact. You'll get an acknowledgement and,
where applicable, a fix and an advisory once it's resolved.

## Hardening notes for operators

- Lidseeker has no TLS of its own — run it behind a reverse proxy that terminates HTTPS, and don't
  expose port 5056 directly to the internet.
- Use a strong, unique `APP_PASSWORD` and a long random `JWT_SECRET`.
- The optional Soularr adapter talks to a locked-down Docker-socket proxy that can **only** restart
  the `soularr` container; the raw Docker socket is never mounted into the app.
