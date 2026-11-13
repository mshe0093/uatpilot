# Security

## Reporting a vulnerability

Please report security issues privately through GitHub's
[private vulnerability reporting](https://docs.github.com/en/code-security/security-advisories/guidance-on-reporting-and-writing-information-about-vulnerabilities/privately-reporting-a-security-vulnerability)
on this repository rather than in a public issue.

## Design decisions that matter for security

- **Target allowlist.** The runner, the generation agent's probes and the OpenAPI loader only
  contact hosts in `UATPILOT_ALLOWED_HOSTS`. A deployed instance can't be used to reach internal
  services (SSRF) or to send traffic to arbitrary third parties. The API accepts OpenAPI specs as
  text, never as a URL to fetch.
- **Read-only public deployments.** With `UATPILOT_WRITE_TOKEN` set, every write endpoint
  (generate, run, reviewer triage) requires `Authorization: Bearer <token>`, and writes are rate
  limited per client.
- **No secrets in the repository.** Configuration comes from environment variables
  (`.env.example` lists them). `.env` is git-ignored.
- **Test credentials.** The only credentials in this repository are the sandboxes' *published*
  demo accounts (Restful-Booker `admin`/`password123`, Sauce Demo `standard_user`/`secret_sauce`),
  which both sites print on their own pages for anyone to use.
- **Model output is untrusted.** Agent-written test cases go through a strict schema before they
  are stored, request paths must be relative to the allowlisted target, and screenshots are served
  only by validated file names.
- **Container.** The image runs as a non-root user, with a health check.
