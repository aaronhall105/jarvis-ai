# Contributing

Jarvis AI is an actively developed personal assistant project.

## Before opening a change

- Search existing issues and pull requests.
- Keep each change focused on one problem.
- Do not include secrets, private household data, access tokens or runtime
  databases.
- Describe the affected component: Core, Android or Home Assistant.
- Include reproduction steps for defects.
- Include validation evidence for code changes.

## Development workflow

1. Create a branch from `conversation-engine`.
2. Make a bounded change.
3. Run the relevant Python or Android tests.
4. Run `git diff --check`.
5. Open a pull request describing behaviour, risk and rollback.

Do not force-push the production branch.
