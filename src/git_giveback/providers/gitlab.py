"""GitLab provider (REST API v4) for gitlab.com and self-managed instances.

* Merge requests and issues come from the global ``/merge_requests`` and
  ``/issues`` endpoints, filtered client-side by created/merged/closed time.
* Pushed commits (one row per project per UTC day) and reviews (approvals, merges and comments on other people's
  MRs) come from the user's activity events. GitLab keeps events for about
  three years, so older ranges will be incomplete.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from urllib.parse import quote, urlparse

from ..dates import in_range, parse_api_datetime, to_utc_iso
from ..http import HttpClient, HttpError
from ..models import AccountResult, Contribution, ProviderError
from . import run_section

REVIEW_RANK = {"approved": 3, "merged": 2, "commented": 1}


def repo_from_web_url(url: str) -> str:
    return urlparse(url).path.strip("/").split("/-/")[0]


class GitLabProvider:
    provider = "gitlab"

    def __init__(self, url: str = "https://gitlab.com", token: str | None = None,
                 username: str | None = None, client=None, verbose: bool = False):
        self.base = url.rstrip("/")
        self.api = f"{self.base}/api/v4"
        self.host = urlparse(self.base).netloc or self.base
        if client is None:
            if not token:
                raise ProviderError(f"no token available for {self.host}")
            client = HttpClient(headers={"PRIVATE-TOKEN": token}, verbose=verbose)
        self.client = client
        self.username = username
        self._projects: dict[int, dict] = {}

    # -- plumbing -----------------------------------------------------------

    def _get(self, path: str, params: dict | None = None):
        return self.client.request("GET", f"{self.api}{path}", params=params).json()

    def _paginate(self, path: str, params: dict) -> list[dict]:
        items: list[dict] = []
        url: str | None = f"{self.api}{path}"
        query: dict | None = {**params, "per_page": 100}
        while url:
            resp = self.client.request("GET", url, params=query)
            items.extend(resp.json() or [])
            url, query = resp.links.get("next"), None
        return items

    def _project(self, project_id: int) -> dict:
        if project_id not in self._projects:
            try:
                self._projects[project_id] = self._get(f"/projects/{project_id}")
            except HttpError:
                self._projects[project_id] = {
                    "path_with_namespace": f"project-{project_id}",
                    "web_url": f"{self.base}/projects/{project_id}",
                }
        return self._projects[project_id]

    def _current_user(self) -> dict:
        if self.username:
            users = self._get("/users", {"username": self.username})
            if not users:
                raise ProviderError(f"GitLab user '{self.username}' not found on {self.host}")
            return users[0]
        return self._get("/user")

    # -- collection ---------------------------------------------------------

    def collect(self, since: datetime, until: datetime) -> AccountResult:
        user = self._current_user()
        result = AccountResult(provider=self.provider, host=self.host, user=user["username"])
        authored_mrs: set[str] = set()
        run_section(result, "merge requests", lambda: self._merge_requests(user, since, until, authored_mrs))
        run_section(result, "issues", lambda: self._issues(user, since, until))
        run_section(result, "activity events", lambda: self._events(user, since, until, authored_mrs))
        return result

    def _merge_requests(self, user: dict, since: datetime, until: datetime, authored: set[str]) -> list[Contribution]:
        items = self._paginate("/merge_requests", {
            "scope": "all", "author_id": user["id"], "updated_after": to_utc_iso(since),
        })
        found = []
        for mr in items:
            authored.add(mr["web_url"])
            created = parse_api_datetime(mr.get("created_at"))
            merged = parse_api_datetime(mr.get("merged_at"))
            if not (in_range(created, since, until) or in_range(merged, since, until)):
                continue
            state = mr.get("state", "")
            if state == "opened":
                state = "draft" if mr.get("draft") or mr.get("work_in_progress") else "open"
            repo = repo_from_web_url(mr["web_url"])
            found.append(Contribution(
                kind="pull_request", repo=repo, title=mr["title"], url=mr["web_url"],
                date=merged if in_range(merged, since, until) else created,
                state=state, role="authored", ref=f"{repo}!{mr['iid']}",
            ))
        return found

    def _issues(self, user: dict, since: datetime, until: datetime) -> list[Contribution]:
        found: dict[str, Contribution] = {}
        searches = (
            ({"author_id": user["id"]}, "authored", "created_at"),
            ({"assignee_id": user["id"], "state": "closed"}, "assignee", "closed_at"),
        )
        for filters, role, date_key in searches:
            items = self._paginate("/issues", {"scope": "all", "updated_after": to_utc_iso(since), **filters})
            for issue in items:
                when = parse_api_datetime(issue.get(date_key))
                if not in_range(when, since, until):
                    continue
                if issue["web_url"] in found:
                    existing = found[issue["web_url"]]
                    if role not in existing.role:
                        existing.role += f", {role}"
                    continue
                repo = repo_from_web_url(issue["web_url"])
                found[issue["web_url"]] = Contribution(
                    kind="issue", repo=repo, title=issue["title"], url=issue["web_url"], date=when,
                    state="open" if issue.get("state") == "opened" else issue.get("state", ""),
                    role=role, ref=f"{repo}#{issue['iid']}",
                )
        return list(found.values())

    def _events(self, user: dict, since: datetime, until: datetime, authored: set[str]) -> list[Contribution]:
        # The events API takes exclusive dates (UTC); widen by a day, then filter exactly.
        after = (since.astimezone(timezone.utc).date() - timedelta(days=1)).isoformat()
        before = (until.astimezone(timezone.utc).date() + timedelta(days=1)).isoformat()
        events = self._paginate(f"/users/{user['id']}/events", {"after": after, "before": before})

        pushes: dict[tuple[int, str], Contribution] = {}  # (project, UTC day)
        reviews: dict[str, Contribution] = {}
        for event in events:
            when = parse_api_datetime(event.get("created_at"))
            if not in_range(when, since, until) or not event.get("project_id"):
                continue
            action = event.get("action_name", "")
            project_id = event["project_id"]

            if action.startswith("pushed"):
                push = event.get("push_data") or {}
                commits = push.get("commit_count") or 0
                if push.get("ref_type") == "tag" or commits <= 0:
                    continue
                day = when.astimezone(timezone.utc).date().isoformat()
                item = pushes.get((project_id, day))
                if item is None:
                    project = self._project(project_id)
                    repo = project["path_with_namespace"]
                    item = pushes[(project_id, day)] = Contribution(
                        kind="commits", repo=repo, title="", ref=repo,
                        url=f"{project['web_url']}/-/commits?author={quote(user['username'])}",
                        date=datetime.fromisoformat(day).replace(tzinfo=timezone.utc), role="pushed", count=0,
                    )
                item.count += commits
                continue

            if action == "approved" and event.get("target_type") == "MergeRequest":
                iid, state = event.get("target_iid"), "approved"
            elif action == "accepted" and event.get("target_type") == "MergeRequest":
                iid, state = event.get("target_iid"), "merged"
            elif action == "commented on" and (event.get("note") or {}).get("noteable_type") == "MergeRequest":
                iid, state = event["note"].get("noteable_iid"), "commented"
            else:
                continue
            if iid is None:
                continue
            project = self._project(project_id)
            url = f"{project['web_url']}/-/merge_requests/{iid}"
            if url in authored:
                continue
            item = reviews.get(url)
            if item is None:
                repo = project["path_with_namespace"]
                reviews[url] = Contribution(
                    kind="review", repo=repo, title=event.get("target_title") or "", url=url, date=when,
                    state=state, role="reviewer", ref=f"{repo}!{iid}",
                )
                continue
            item.count += 1
            item.date = max(item.date, when)
            if REVIEW_RANK[state] > REVIEW_RANK.get(item.state, 0):
                item.state = state
        return list(pushes.values()) + list(reviews.values())
