# Contributing to billbrake

Thanks for helping! Issues and PRs are welcome.

## Setup
```bash
python -m venv .venv && . .venv/bin/activate
pip install -e '.[dev]'
pytest && ruff check .
```

## Ground rules
- Every behaviour change needs a test. Provider calls are always mocked (`httpx.MockTransport`,
  `botocore.stub.Stubber`, fake clients) – tests must never hit a real API or spend money.
- Kill switches must keep **dry-run as the default**.
- Price table changes: link the provider's pricing page in the PR.
- Keep dependencies small; the proxy should stay easy to read.

## Layout
- `src/billbrake/` – proxy, config, SQLite ledger, CLI
- `killswitch/aws`, `killswitch/gcp` – cloud functions
- `terraform/` – deployment for the kill switches
- `examples/` – sample config and clients
