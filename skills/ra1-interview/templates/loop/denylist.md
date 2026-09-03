# Loop Denylist

This denylist documents actions and paths a loop runner must block or require human confirmation for. RA1 checks that a starter policy exists; the loop runner is responsible for enforcement.

## Starter blocked actions

- Never read or write `.env*`, `**/*.pem`, `**/*.key`, `**/*secret*`, or credential exports.
- Never mutate `.git/**` directly.
- Never run destructive deletes outside the project root.
- Never run `git push`, `gh pr merge`, release publication, or deploy commands without explicit human confirmation.
- Never disable CI, security scanning, branch protection, audit logging, or kill switches.
