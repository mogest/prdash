# prdash

A terminal dashboard for your GitHub pull requests. See your open PRs, what's waiting for review, what needs your review, and what's approved and ready to merge — all in one glance.

![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue)

## Features

- **Your PRs not in review** — open PRs that haven't been sent for review yet
- **Your PRs waiting for review** — PRs you've requested reviews on, with reviewer names
- **PRs waiting for your review** — PRs where you or your team have been requested as reviewer
- **Approved PRs** — your PRs that are approved but not yet merged
- CI/check status for every PR (pass/fail/pending/running)
- Clickable PR links in supported terminals
- Watch mode with automatic refresh and change highlighting
- Parallel fetching across all configured repos

## Requirements

- Python 3.11+
- [GitHub CLI](https://cli.github.com/) (`gh`) installed and authenticated

## Install

Homebrew:

```
brew install mogest/tap/prdash
```

pipx:

```
pipx install prdash
```

uv:

```
uv tool install prdash
```

## Configuration

On first run, prdash will prompt you to set up your config interactively. The config is stored at `~/.config/prdash.toml` (or `$XDG_CONFIG_HOME/prdash.toml`).

```toml
user = "your-github-username"
repos = ["org/repo1", "org/repo2"]
teams = ["team-name"]  # optional
```

| Key | Description |
|-----|-------------|
| `user` | Your GitHub username |
| `repos` | List of repositories to monitor |
| `teams` | GitHub team names you belong to (used for team review requests) |

## Usage

```
prdash
```

Watch mode, refreshing every 30 seconds:

```
prdash -w 30
```

In watch mode, PRs that move between sections (e.g. from "waiting for review" to "approved") are highlighted.

## License

MIT
