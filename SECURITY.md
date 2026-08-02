# Security policy

## Reporting a vulnerability

Do not disclose credentials, private URLs, household data or exploitable details
in a public issue.

Use GitHub's private vulnerability-reporting or Security Advisory feature for
this repository. Include:

- A concise description.
- Affected component and version.
- Reproduction steps.
- Security impact.
- Suggested mitigation when known.

## Sensitive data

The following must never be committed:

- `.env`
- API keys and access tokens
- Home Assistant long-lived tokens
- Private keys or signing files
- Runtime databases
- Household camera images
- Personal conversation logs

Rotate any credential immediately if it is exposed.
