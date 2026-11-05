# git-giveback

List **your** Git contributions — pull/merge requests, code reviews, issues and
commits — across GitHub and GitLab for any date range. Handy for self-reviews,
promotion packets, and community "giveback" reporting.

It runs locally with your own tokens. There is no server, no database, and
nothing to sign up for.

```
$ ./git-giveback --since 2026-08-01 --until 2026-10-31
Git contributions 2026-08-01 to 2026-10-31 (EST)

github.com · @lennysh
  22 pull requests (17 merged, 2 open, 3 draft) · 13 reviewed · 205 commits in 23 repos

  Pull requests (22)
    2026-10-27  merged  redhat-cop/infra.aap_configuration#1466  AAPRFE-2117 - Enable 'check mode' ...
    2026-10-14  open    ansible/ansible-ui#3752                  Update EDA rulebook activation help text ...
    ...
  Reviews (13)
    2026-10-07  approved  ansible/awx#16700  Support Galaxy Service Account credentials in project sync
    ...
  Commits (23)
    2026-10-27  3 commits   redhat-cop/infra.aap_configuration
    ...
```

## Quick start

Requires **Python 3.11+**. No third-party packages are needed.

```bash
git clone https://github.com/lennysh/git-giveback.git
cd git-giveback

# GitHub: reuses your `gh auth login`, or set a token
export GITHUB_TOKEN=ghp_...
# GitLab (optional)
export GITLAB_TOKEN=glpat-...
export GITLAB_URL=https://gitlab.com      # or your self-managed instance

./git-giveback --since 2026-01-01 --until 2026-06-30
```

You can also install it as a command:

```bash
uv tool install git+https://github.com/lennysh/git-giveback   # or: pipx install ...
git-giveback --days 90
git giveback --days 90      # works as a git subcommand once it's on your PATH
```

Or run it in a container:

```bash
podman build -t git-giveback .
podman run --rm -e GITHUB_TOKEN git-giveback --since 2026-01-01
```

## What is reported

| Section | GitHub | GitLab |
|---|---|---|
| **Pull / merge requests** | PRs you authored that were created *or* merged in the range, with state (merged / open / draft / closed) | Same, for MRs |
| **Reviews** | PRs by other people you reviewed in the range, with your strongest verdict (approved > changes requested > commented) | MRs by other people you approved, merged, or commented on |
| **Issues** | Issues you opened in the range, and issues assigned to you that were closed in the range | Same |
| **Commits** | Commits per repository, as counted by your GitHub contribution graph | Commits you *pushed*, per project, from your activity events |

## Usage

```
git-giveback [--since DATE | --days N] [--until DATE] [--utc]
             [--github [HOST]] [--gitlab [URL]] [--user NAME] [--config PATH]
             [--include REGEX] [--exclude REGEX] [--type TYPE]
             [--format table|markdown|json|csv] [--output FILE] [--verbose]
```

* **Dates** can be `YYYY-MM-DD` or a full ISO 8601 datetime. A plain `--until`
  date includes that whole day. Times use your local time zone unless you pass
  `--utc`. With no dates, the last 30 days are reported.
* **`--format markdown`** produces a linked list you can paste into a review
  doc. `json` and `csv` are for spreadsheets and scripts.
* **`--include` / `--exclude`** filter on the repository's full name, e.g.
  `--include '^redhat-cop/'` or `--exclude '^lennysh/'` to skip personal repos.
  Both can be repeated.
* **`--type`** limits the output to `pull_request`, `review`, `issue` or
  `commits`. It can be repeated.
* **`--user`** reports on someone else. Only activity your token can see is
  included, which usually means public activity.

Examples:

```bash
# Fiscal-year giveback report as Markdown
git-giveback --since 2026-03-01 --until 2027-02-28 -f markdown -o fy27-giveback.md

# Only upstream / community work, no personal repos
git-giveback --days 180 --exclude '^lennysh/'

# GitHub plus a self-managed GitLab
git-giveback --github --gitlab https://gitlab.example.com --since 2026-01-01
```

The exit code is `0` on success and `1` if any account had errors. Partial
results are still printed when that happens.

## Accounts and tokens

Accounts are chosen in this order:

1. `--github` / `--gitlab` flags on the command line
2. The config file `~/.config/git-giveback/config.toml` (see
   [`config.example.toml`](config.example.toml)). Use this for several
   accounts, GitHub Enterprise, or self-managed GitLab.
3. Auto-detection: github.com if a GitHub token is found, and GitLab if
   `GITLAB_TOKEN` is set

Tokens are looked up in this order: `token_env` from the config file, then
`GH_TOKEN`/`GITHUB_TOKEN` (`GH_ENTERPRISE_TOKEN` for Enterprise hosts) or
`GITLAB_TOKEN`, then the `gh` / `glab` CLI login.

### Token permissions

* **GitHub:** a classic token with `repo` and `read:org` scopes, or a
  fine-grained token with read access to the repositories you care about.
  `gh auth login` already has what it needs. Public-only reporting works with
  no scopes at all.
* **GitLab:** a personal access token with `read_api`.

### SAML SSO organizations (important for Red Hatters)

Many company GitHub organizations enforce SAML single sign-on. A token that is
**not SSO-authorized** for an organization quietly misses that organization's
private repositories. To fix this, go to
[github.com/settings/tokens](https://github.com/settings/tokens), click
**Configure SSO** next to your token, and authorize each organization. When
GitHub reports this, git-giveback shows it as a `note:` at the end of that
account's section.

## Known limitations

* **GitHub commits** use the contribution-graph rules. They count commits on a
  repository's default branch (or `gh-pages`) and not in forks, and are
  bucketed by day. Commits in private repositories the token can't see appear
  only as a count in a note.
* **GitHub search** returns at most 1000 results per query. git-giveback
  splits busy date ranges automatically, so this only matters for more than
  1000 PRs in a single day.
* **GitLab commits** are *pushed* commits from activity events, so force-pushes
  and pushes of other people's commits are included. GitLab keeps events for
  about **three years**, so commits and reviews older than that are missing.
* **GitLab reviews** come from approvals, merges, and comments on other
  people's MRs. Plain "reviewer assigned" with no action is not counted.

## Development

```bash
python3 -m unittest discover -s tests -t . -v
```

The code lives in `src/git_giveback/`:

| Module | Purpose |
|---|---|
| `cli.py` | Argument parsing, account selection, filtering |
| `providers/github.py` | GitHub GraphQL: contributionsCollection and search |
| `providers/gitlab.py` | GitLab REST v4: MRs, issues, user events |
| `output.py` | table / markdown / json / csv renderers |
| `config.py` | Config file and token discovery |
| `http.py` | Small urllib client with rate-limit retries |

Adding a forge (Gitea/Forgejo, Bitbucket, ...) means writing a provider with a
`collect(since, until) -> AccountResult` method and wiring it up in
`cli.make_provider`.

## History

git-giveback is a spiritual successor to the Red Hat Community of Practice
*Giveback Ninja* program
([ninja-points](https://github.com/redhat-cop/ninja-points) and
[ninja-board](https://github.com/redhat-cop/ninja-board)). Those tools scanned
whole organizations from a central server to award points. git-giveback turns
that around: each person runs it for themselves, across every repository they
contribute to.

## License

[MIT](LICENSE)
