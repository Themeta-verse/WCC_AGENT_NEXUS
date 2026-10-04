# Security Policy

## Scope

Nexus treats the frontend, uploaded content, datasets, model artifacts, external responses, agents, and natural-language instructions as untrusted. Sprint 1 protects the HTTP and persistence foundation; it does not claim to secure future ML or deployment features that are not implemented.

## Non-negotiable repository rules

The repository must never contain API keys, passwords, access tokens, private keys, signing keys, encryption keys, production credentials, sensitive datasets, private model artifacts, or logs containing secrets. `.env` files are prohibited, including sample files that could later receive real credentials. Runtime secrets must come from a managed deployment secret facility.

Privileged secrets must never be sent to browser code, browser storage, source maps, public configuration, or API responses. Authentication and authorization are server-side responsibilities; client-side checks are not security controls.

## Current controls

Sprint 1 provides real password hashing, opaque expiring sessions, session revocation, resource-level project authorization, foreign-key enforcement, request IDs, bounded request metadata, safe error responses, security headers, authentication rate limiting, and transactional audit records for security-sensitive mutations.

Audit metadata must remain non-sensitive. Do not add request bodies, authorization headers, passwords, tokens, raw datasets, or private model inputs to logs or audit records.

## Reporting

Do not open a public issue containing a suspected vulnerability or secret. Revoke and rotate any exposed credential immediately, preserve relevant evidence without copying sensitive values into Git, and report the incident through the repository owner’s private security channel.

## Validation gates

Before every authorized push, run the test suite and linter, inspect the Git diff, verify that no `.env` file exists, search for common credential patterns, and verify that generated local databases and logs are ignored. A passing test suite is necessary but does not prove standards compliance or production readiness.
