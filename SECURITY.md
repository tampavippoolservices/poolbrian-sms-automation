# Security policy

Do not open a public issue containing credentials, customer information, message bodies, OAuth
tokens, database URLs, or provider payloads. Report security concerns privately to the Tampa VIP
Pool Services application owner.

Before sharing logs, remove authorization headers, signatures, tokens, email addresses, and full
phone numbers. Rotate an exposed secret immediately in the provider and in every Render service that
uses it. Treat a committed `TOKEN_ENCRYPTION_KEY` as a database credential incident because it can
decrypt stored OAuth refresh tokens.

Production changes require passing CI, a database backup, a reviewed migration, and the controlled
cutover procedure in `docs/DEPLOYMENT.md`.
