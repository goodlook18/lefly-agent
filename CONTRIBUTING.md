# Contributing To LeFly Agent

Contributions to the source Alpha are welcome.

## Contribution workflow

1. Search existing issues before starting work.
2. Open an issue first for substantial behavior, protocol, dependency, or UI
   changes so the scope can be agreed before implementation.
3. Create a focused branch, add or update tests, and keep unrelated changes out.
4. Open a pull request describing the change, validation performed, and any
   third-party source, dependency, or media introduced.

## Development setup

- Use Python 3.12 for the complete release gate.
- Use Node.js 22.12 or newer for Web Console work.
- Keep hardware libraries and third-party robot runtimes outside clean-core packages.
- Add a failing test before changing behavior.
- Never commit credentials, `.env` files, logs, recordings, or local runtime state.
- Record the origin and license of every third-party source or media asset.

Install the Python packages into the active environment:

```bash
python -m pip install -e 'packages/lefly-protocol[test]' \
  -e packages/lefly-sdk-python \
  -e packages/lefly-simulator \
  -e 'packages/lefly-agent[llm]'
```

Install Console dependencies:

```bash
cd packages/lefly-console-web
npm ci
```

## Checks

```bash
python -m unittest discover -s tests -v
python -m unittest discover -s packages/lefly-simulator/tests -v
python -m unittest discover -s packages/lefly-agent/tests -v
python tools/audit_open_source_boundary.py packages tests tools
python tools/check_release_versions.py --root . --expected 0.1.0
python tools/check_public_release.py --root . --skip-inventory

cd packages/lefly-console-web
npm test
npm run build
```

Changes to public contracts should include positive and negative fixtures.
Changes to the Console should include interaction and responsive regression
coverage. Changes to a bundled dependency or asset must update the public
third-party notices and document its origin and license in the contribution;
maintainers update release provenance when preparing a release.

Do not edit `.lefly-release-inventory.json` in an ordinary pull request. Branch
CI checks the public boundary without requiring frozen release digests. When a
maintainer prepares a version tag, regenerate and verify the inventory:

```bash
python tools/update_release_inventory.py \
  --root . \
  --release-version 0.1.0
python tools/check_public_release.py --root .
```

Tag CI enforces the frozen inventory. The private development upstream remains
the source of truth for shared files; maintainers reconcile accepted public
changes there before regenerating the public release mirror.

By submitting a contribution, you agree that it is licensed under Apache-2.0.
