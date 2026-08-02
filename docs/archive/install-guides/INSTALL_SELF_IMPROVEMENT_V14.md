# Jarvis Self-Improvement Engine v14 — Installation

This is a **Jarvis Ubuntu/Core-only update**. It does not replace the Home
Assistant Conversation integration and does not require a Home Assistant restart.

## What the installer does

- Creates a protected host-side improvement worker.
- Creates a Python virtual environment for code generation and testing.
- Adds self-improvement settings to `.env` without exposing its contents.
- Commits the current working Jarvis code as the safe Git baseline.
- Rebuilds Jarvis Core.
- Installs and starts a per-user `systemd` service.
- Leaves automatic source-code deployment disabled.

## 1. Upload and extract

Upload `jarvis-self-improvement-engine-v14-core.tar.gz` to:

`/home/aaron/jarvis/`

Run on the Ubuntu terminal showing `aaron@arvis`:

```bash
cd ~/jarvis

mkdir -p backup/self-improvement-v14

cp bridge/app/config.py \
  backup/self-improvement-v14/config.py.before-v14

cp bridge/app/main.py \
  backup/self-improvement-v14/main.py.before-v14

[ -f bridge/app/self_improvement.py ] && \
  cp bridge/app/self_improvement.py \
  backup/self-improvement-v14/self_improvement.py.before-v14 || true

cp .gitignore \
  backup/self-improvement-v14/gitignore.before-v14 2>/dev/null || true

tar -xzf jarvis-self-improvement-engine-v14-core.tar.gz
```

## 2. Compile the new Python files

```bash
cd ~/jarvis

python3 -m py_compile \
  bridge/app/self_improvement.py \
  bridge/app/config.py \
  bridge/app/main.py \
  tools/self_improvement_worker.py

bash -n tools/install_self_improvement_v14.sh
bash -n tools/jarvis-improve
```

No output means the checks passed.

## 3. Run the installer

```bash
cd ~/jarvis

chmod +x \
  tools/install_self_improvement_v14.sh \
  tools/jarvis-improve \
  tools/self_improvement_worker.py

./tools/install_self_improvement_v14.sh
```

The installer may take several minutes while it creates the isolated Python
environment and installs testing/security tools.

If it reports that Python venv support is missing:

```bash
sudo apt update
sudo apt install -y python3-venv

cd ~/jarvis
./tools/install_self_improvement_v14.sh
```

## 4. Keep the worker running after logout

Run once:

```bash
sudo loginctl enable-linger "$USER"
```

Then verify:

```bash
systemctl --user restart jarvis-improver
systemctl --user --no-pager --full status jarvis-improver
```

## 5. Verify Jarvis Core and the worker

```bash
cd ~/jarvis

curl -s http://localhost:8000/health
printf '\n'

./tools/jarvis-improve status

curl -s http://localhost:8000/api/improvement/status
printf '\n'
```

Expected Jarvis Core version: `2.1.0`.

The improvement status should show that the feature and worker are enabled. The
worker heartbeat may take up to the configured polling interval to appear.

## 6. First safe test

In Home Assistant Assist, from Aaron's administrator account, say:

```text
Show self-improvement status.
```

Then deliberately record a harmless test failure:

```text
Record that as a mistake.
```

Review the queue:

```text
Show recorded mistakes.
```

Ask Jarvis to prepare a candidate:

```text
Prepare a fix for the last mistake.
```

The worker will not modify the live code. It creates an isolated Git worktree,
generates a bounded patch, runs local tests/security checks, starts a restricted
candidate container, and performs an independent review.

When a candidate passes, Jarvis sends Aaron a notification containing its ID and
six-digit approval code.

Review it:

```text
Show pending improvements.
```

Deploy only after reviewing the summary:

```text
Deploy improvement 12 code 123456.
```

Use the actual candidate ID and code supplied by Jarvis.

## Emergency stop

Voice/chat command:

```text
Emergency stop self-improvement.
```

Host command:

```bash
cd ~/jarvis

touch data/self_improvement.disabled
systemctl --user stop jarvis-improver
```

Jarvis Core and normal Home Assistant control continue running.

Resume:

```bash
cd ~/jarvis

rm -f data/self_improvement.disabled
systemctl --user start jarvis-improver
```

Or say:

```text
Resume self-improvement.
```

## GitHub hardening

The package installs Jarvis CI, CodeQL, CODEOWNERS and a pull-request template.
After pushing the v14 baseline, protect the production branch in GitHub:

- Require a pull request before merging.
- Require Aaron's approval.
- Require Jarvis CI and CodeQL status checks.
- Dismiss stale approvals after new commits.
- Block force pushes and branch deletion.
- Apply the rules to administrators.

Remote pull-request creation is disabled by default. Enable it only after the
GitHub CLI is authenticated:

```bash
cd ~/jarvis

gh auth status
sed -i \
  's/^JARVIS_IMPROVEMENT_GITHUB_ENABLED=.*/JARVIS_IMPROVEMENT_GITHUB_ENABLED=true/' \
  .env

systemctl --user restart jarvis-improver
```

## Rollback v14 installation

The installer commits the current baseline. The preferred rollback is Git:

```bash
cd ~/jarvis

git log --oneline -n 5
```

Select the commit immediately before `Install Jarvis Self-Improvement Engine v14`,
then:

```bash
cd ~/jarvis

systemctl --user disable --now jarvis-improver

git reset --hard <PREVIOUS_COMMIT_SHA>

docker compose up -d --build
```

Do not paste a placeholder SHA literally.
