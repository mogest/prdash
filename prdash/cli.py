import argparse
from datetime import datetime
import io
import json
import os
import subprocess
import sys
import time
import tomllib
from concurrent.futures import ThreadPoolExecutor

CONFIG_PATH = os.path.join(
    os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config")),
    "prdash.toml",
)

USER = None
REPOS = None
TEAMS = None

GREEN = "\033[32m"
RED = "\033[31m"
YELLOW = "\033[33m"
CYAN = "\033[36m"
BLUE = "\033[38;5;75m"
DARK_GREY = "\033[38;5;240m"
MID_GREY = "\033[38;5;245m"
LIGHT_GREY = "\033[37m"
BOLD = "\033[1m"
RESET = "\033[0m"

HIGHLIGHT = "\033[48;5;23;37m"
NONE_MSG = f"{MID_GREY}— none —{RESET}"


def load_config():
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, "rb") as f:
            config = tomllib.load(f)
        for key in ("user", "repos"):
            if key not in config:
                print(f"error: '{key}' missing from {CONFIG_PATH}", file=sys.stderr)
                sys.exit(1)
        return config

    print(f"{BOLD}{CYAN}First-time setup for prdash{RESET}\n")

    default_user = ""
    try:
        result = subprocess.run(
            ["gh", "api", "/user", "--jq", ".login"],
            capture_output=True, text=True,
        )
        if result.returncode == 0:
            default_user = result.stdout.strip()
    except FileNotFoundError:
        pass

    print(f"{BOLD}GitHub user{RESET}")
    if default_user:
        user = input(f"  username [{default_user}]: ").strip() or default_user
    else:
        user = input("  username: ").strip()
    if not user:
        print(f"{RED}error: user is required{RESET}", file=sys.stderr)
        sys.exit(1)

    print(f"\n{BOLD}Repos to monitor{RESET}")
    print(f"{MID_GREY}  Enter repos in org/repo format, one per line. Blank to finish.{RESET}")
    repos = []
    while True:
        repo = input("  repo: ").strip()
        if not repo:
            if not repos:
                print(f"  {YELLOW}at least one repo is required{RESET}")
                continue
            break
        result = subprocess.run(
            ["gh", "repo", "view", repo, "--json", "name"],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            print(f"  {RED}'{repo}' not found or not accessible, try again{RESET}")
            continue
        print(f"  {GREEN}added{RESET}")
        repos.append(repo)

    print(f"\n{BOLD}Teams{RESET}")
    print(f"{MID_GREY}  PRs requesting review from your teams will show in your dashboard.{RESET}")
    teams = []
    repo_orgs = {r.split("/")[0] for r in repos}
    try:
        result = subprocess.run(
            ["gh", "api", "/user/teams", "--jq", r'.[] | "\(.organization.login)\t\(.name)"'],
            capture_output=True, text=True,
        )
        if result.returncode == 0 and result.stdout.strip():
            detected = []
            for line in result.stdout.strip().splitlines():
                org, name = line.split("\t", 1)
                if org in repo_orgs:
                    detected.append(name)
            if detected:
                print(f"  detected: {CYAN}{', '.join(detected)}{RESET}")
                keep = input("  use these teams? [Y/n]: ").strip().lower()
                if keep != "n":
                    teams = detected
    except FileNotFoundError:
        pass

    if not teams:
        teams_input = input("  teams (comma-separated, blank to skip): ").strip()
        teams = [t.strip() for t in teams_input.split(",") if t.strip()]

    os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
    with open(CONFIG_PATH, "w") as f:
        f.write(f'user = "{user}"\n')
        f.write("repos = [{}]\n".format(", ".join(f'"{r}"' for r in repos)))
        if teams:
            f.write("teams = [{}]\n".format(", ".join(f'"{t}"' for t in teams)))

    print(f"\n{GREEN}Config saved to {CONFIG_PATH}{RESET}\n")
    return {"user": user, "repos": repos, "teams": teams}


def link(url, label):
    return f"\033]8;;{url}\a{label}\033]8;;\a"


def get_my_prs(repo):
    result = subprocess.run(
        [
            "gh", "pr", "list", "-R", repo,
            "--author", USER,
            "--json", "number,title,reviewRequests,baseRefName,headRefName,url,statusCheckRollup,latestReviews",
        ],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        print(f"warning: failed to query {repo}: {result.stderr.strip()}", file=sys.stderr)
        return [], [], []

    not_in_review = []
    waiting = []
    approved = []
    for pr in json.loads(result.stdout):
        if any(r.get("state") == "APPROVED" for r in pr.get("latestReviews", [])):
            approved.append((repo, pr))
        elif pr.get("reviewRequests"):
            waiting.append((repo, pr))
        else:
            not_in_review.append((repo, pr))
    return not_in_review, waiting, approved


def get_prs(repo):
    result = subprocess.run(
        [
            "gh", "pr", "list", "-R", repo,
            "--json", "number,title,author,reviewRequests,baseRefName,headRefName,url,statusCheckRollup",
        ],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        print(f"warning: failed to query {repo}: {result.stderr.strip()}", file=sys.stderr)
        return []

    rows = []
    for pr in json.loads(result.stdout):
        if pr.get("author", {}).get("login") == USER:
            continue
        if any(r.get("login") == USER or r.get("name") in TEAMS for r in pr.get("reviewRequests", [])):
            rows.append((repo, pr))
    return rows


def check_status(rollup):
    if not rollup:
        return "—", None

    total = len(rollup)
    passed = failed = 0
    for check in rollup:
        state = check.get("conclusion") or check.get("state") or "PENDING"
        if state in ("SUCCESS", "NEUTRAL", "SKIPPED"):
            passed += 1
        elif state in ("FAILURE", "ERROR", "CANCELLED", "TIMED_OUT", "ACTION_REQUIRED"):
            failed += 1

    if failed > 0:
        return f"fail • {passed}/{total}", RED
    elif passed == total:
        return f"pass • {total}/{total}", GREEN
    elif passed + failed == 0:
        return "pending", YELLOW
    else:
        return f"running • {passed}/{total}", YELLOW


def print_table(columns, rows, highlighted=None, file=None):
    highlighted = highlighted or set()
    widths = {key: len(header) for header, key in columns}
    for row in rows:
        for _, key in columns:
            widths[key] = max(widths[key], len(row[key]))

    gap = "  "
    header = gap.join(h.ljust(widths[k]) for h, k in columns)
    print(f"{MID_GREY}{header}{RESET}", file=file)

    for row in rows:
        highlight = row["pr"] in highlighted
        parts = []
        for _, key in columns:
            val = row[key].ljust(widths[key])
            if not highlight:
                if key == "checks" and row.get("checks_color"):
                    val = row["checks_color"] + val + RESET
                elif key == "pr" and row.get("url"):
                    val = BLUE + val + RESET
            parts.append(val)
        line = gap.join(parts)
        if row.get("url"):
            line = link(row["url"], line)
        if highlight:
            line = f"{HIGHLIGHT}{line}\033[K{RESET}"
        print(line, file=file)


def fetch_data():
    with ThreadPoolExecutor() as pool:
        review_futures = {repo: pool.submit(get_prs, repo) for repo in REPOS}
        my_futures = {repo: pool.submit(get_my_prs, repo) for repo in REPOS}

    not_in_review_rows = []
    my_waiting_rows = []
    approved_rows = []
    for repo in REPOS:
        not_in_review, waiting, approved = my_futures[repo].result()
        for repo_name, pr in not_in_review:
            checks_text, checks_color = check_status(pr.get("statusCheckRollup", []))
            base = pr['baseRefName']
            branch = pr['headRefName'] if base == "main" else f"{pr['headRefName']} -> {base}"
            not_in_review_rows.append({
                "pr": f"{repo_name.split('/')[-1]}#{pr['number']}",
                "title": pr["title"],
                "branch": branch,
                "checks": checks_text,
                "checks_color": checks_color,
                "url": pr["url"],
            })
        for repo_name, pr in waiting:
            checks_text, checks_color = check_status(pr.get("statusCheckRollup", []))
            reviewers = ", ".join(
                r.get("login") or r.get("name", "") for r in pr.get("reviewRequests", [])
            )
            base = pr['baseRefName']
            branch = pr['headRefName'] if base == "main" else f"{pr['headRefName']} -> {base}"
            my_waiting_rows.append({
                "pr": f"{repo_name.split('/')[-1]}#{pr['number']}",
                "title": pr["title"],
                "branch": branch,
                "checks": checks_text,
                "checks_color": checks_color,
                "reviewer": reviewers,
                "url": pr["url"],
            })
        for repo_name, pr in approved:
            checks_text, checks_color = check_status(pr.get("statusCheckRollup", []))
            approved_by = ", ".join(
                r["author"]["login"] for r in pr.get("latestReviews", []) if r.get("state") == "APPROVED"
            )
            base = pr['baseRefName']
            branch = pr['headRefName'] if base == "main" else f"{pr['headRefName']} -> {base}"
            approved_rows.append({
                "pr": f"{repo_name.split('/')[-1]}#{pr['number']}",
                "title": pr["title"],
                "branch": branch,
                "checks": checks_text,
                "checks_color": checks_color,
                "approved_by": approved_by,
                "url": pr["url"],
            })

    review_rows = []
    for repo in REPOS:
        for repo_name, pr in review_futures[repo].result():
            checks_text, checks_color = check_status(pr.get("statusCheckRollup", []))
            reviewers = ", ".join(
                r.get("login") or r.get("name", "") for r in pr.get("reviewRequests", [])
            )
            base = pr['baseRefName']
            branch = pr['headRefName'] if base == "main" else f"{pr['headRefName']} -> {base}"
            review_rows.append({
                "pr": f"{repo_name.split('/')[-1]}#{pr['number']}",
                "title": pr["title"],
                "author": pr["author"]["login"],
                "branch": branch,
                "checks": checks_text,
                "checks_color": checks_color,
                "reviewer": reviewers,
                "url": pr["url"],
            })

    tables = {
        "not_in_review": not_in_review_rows,
        "waiting": my_waiting_rows,
        "review": review_rows,
        "approved": approved_rows,
    }
    return tables


def render(tables, out, highlighted=None):
    not_in_review_rows = tables["not_in_review"]
    my_waiting_rows = tables["waiting"]
    review_rows = tables["review"]
    approved_rows = tables["approved"]

    p = lambda *args, **kwargs: print(*args, **kwargs, file=out)

    p(f"{BOLD}{CYAN}My open PRs not in review{RESET}")
    if not_in_review_rows:
        print_table([
            ("PR", "pr"),
            ("TITLE", "title"),
            ("BRANCH", "branch"),
            ("CHECKS", "checks"),
        ], not_in_review_rows, highlighted=highlighted, file=out)
    else:
        p(NONE_MSG)

    p()
    p(f"{BOLD}{CYAN}My PRs waiting for review{RESET}")
    if my_waiting_rows:
        print_table([
            ("PR", "pr"),
            ("TITLE", "title"),
            ("BRANCH", "branch"),
            ("CHECKS", "checks"),
            ("REVIEWER", "reviewer"),
        ], my_waiting_rows, highlighted=highlighted, file=out)
    else:
        p(NONE_MSG)

    p()
    p(f"{BOLD}{CYAN}PRs waiting for my review{RESET}")
    if review_rows:
        print_table([
            ("PR", "pr"),
            ("TITLE", "title"),
            ("AUTHOR", "author"),
            ("BRANCH", "branch"),
            ("CHECKS", "checks"),
            ("REVIEWER", "reviewer"),
        ], review_rows, highlighted=highlighted, file=out)
    else:
        p(NONE_MSG)

    p()
    p(f"{BOLD}{CYAN}My approved unmerged PRs{RESET}")
    if approved_rows:
        print_table([
            ("PR", "pr"),
            ("TITLE", "title"),
            ("BRANCH", "branch"),
            ("CHECKS", "checks"),
            ("APPROVED BY", "approved_by"),
        ], approved_rows, highlighted=highlighted, file=out)
    else:
        p(NONE_MSG)


def main():
    global USER, REPOS, TEAMS

    config = load_config()
    REPOS = config["repos"]
    USER = config["user"]
    TEAMS = config.get("teams", [])

    parser = argparse.ArgumentParser(description="Show PR status dashboard")
    parser.add_argument("-w", "--watch", type=int, metavar="SECONDS",
                        help="refresh every SECONDS seconds")
    args = parser.parse_args()

    def table_assignments(tables):
        return {row["pr"]: name for name, rows in tables.items() for row in rows}

    try:
        if args.watch:
            prev_assign = None
            while True:
                tables = fetch_data()
                curr_assign = table_assignments(tables)
                highlighted = set()
                if prev_assign is not None:
                    for pr, table_name in curr_assign.items():
                        if prev_assign.get(pr) != table_name:
                            highlighted.add(pr)
                prev_assign = curr_assign
                out = io.StringIO()
                render(tables, out, highlighted=highlighted)
                cols = os.get_terminal_size().columns
                timestamp = datetime.now().strftime("%H:%M:%S")
                content = out.getvalue()
                print("\033[2J\033[H", end="")
                print(content, end="")
                print(f"\033[s\033[1;{cols - len(timestamp) + 1}H{DARK_GREY}{timestamp}{RESET}\033[u", end="", flush=True)
                time.sleep(args.watch)
        else:
            tables = fetch_data()
            render(tables, sys.stdout)
    except KeyboardInterrupt:
        print()
