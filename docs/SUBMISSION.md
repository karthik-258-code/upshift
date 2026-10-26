# Submission plan

Deadline: Friday 30 October 2026, 10:00 PDT (22:30 IST). Internal freeze: Thursday 29
October, 18:00 IST. Judging 1 to 15 December; the hosted replay must stay up until then (it
is GitLab Pages, so it will).

## What gets submitted

| Item | Where |
| --- | --- |
| Repository | https://gitlab.com/notorandyt/upshift |
| Hosted demo (recorded replay of real runs) | https://notorandyt.gitlab.io/upshift/ |
| Merge requests Upshift opened | pyshop-legacy [!1](https://gitlab.com/notorandyt/pyshop-legacy/-/merge_requests/1) (pydantic), [!2](https://gitlab.com/notorandyt/pyshop-legacy/-/merge_requests/2) (SQLAlchemy); flask-notes [!1](https://gitlab.com/notorandyt/flask-notes/-/merge_requests/1) (Flask) |
| Video (under 3 minutes, public on YouTube) | link to be added |
| Track | Coding and Agentic Engineering |
| Also targeting | Best Use of Tavily; Most Valuable Feedback ([docs/FEEDBACK.md](FEEDBACK.md)) |
| Results | [README](../README.md#results), [evals/results/](../evals/results/) |
| Architecture | [docs/ARCHITECTURE.md](ARCHITECTURE.md) |

Everything linked is public and lives under `notorandyt`: the repository, its Pages site,
and the two demo projects (`notorandyt/pyshop-legacy`, `notorandyt/flask-notes`) that hold
the merge requests. Check each link logged out before submitting.

## Checklist before freeze (29 Oct, 18:00 IST)

- [x] `pytest -q` and `ruff check .` green on `dev`; CI pipeline green (2026-10-26).
- [x] Showcase upgrades re-run into public projects and their recordings exported with
      explicit `--ids` (never `--succeeded N`, which drops older showcase runs from the
      index); Pages redeployed (2026-10-26).
- [x] Eval table in README matches `evals/results/latest.md` exactly.
- [ ] README quick start followed from a clean clone on one machine (venv, npm build,
      probe, one run).
- [ ] Video under 3 minutes, public on YouTube, link in this file and in the README.
- [ ] FEEDBACK.md read end to end by the author; nothing in it we did not observe.
- [x] Showcase MRs (pyshop-legacy !1, !2; flask-notes !1) open, public, not merged
      (2026-10-26).
- [ ] `.env` never committed (`git log --all -p -- .env` empty).

## Video script (target 2:50; the rules require under 3 minutes, public on YouTube)

Record at 1920x1080, browser zoomed to 110%, dark theme, no notifications. Voice over the
screen; no face cam needed. Record the live run segment in real time and cut the waiting;
keep timestamps visible so the compression is honest.

About 290 words of narration; at a normal pace that fits 2:50 with room for pauses. Cut
the live-run waiting hard. If the final edit runs over 2:58, shorten the live run first.

**0:00 to 0:15. Hook.** Screen: pyshop-legacy MR !1, the Changes tab, scrolled to
`models.py`. "This merge request upgraded pydantic from 1 to 2 in a legacy codebase.
Seventeen tests pass, a second model reviewed it, and no human wrote a line of it.
Dependabot opens the PR; Upshift finishes it."

**0:15 to 0:35. The problem.** Screen: MR !1 description, scrolled to the "Code changes and
why" table. "The version bump is the easy part. The work is finding which documented
changes touch your code, rewriting it, and proving nothing broke. Every edit here links to
the changelog line that justifies it; the bare bump left zero tests running."

**0:35 to 1:35. Live run.** Screen: Mission Control at localhost:8000 (live mode). Click
**Start upgrade run** with the pre-filled pydantic case; cut the waiting. "The Scout maps
which pydantic symbols the code uses. The Historian, Nemotron 3.5 Lightning with a
million-token context, reads the whole migration guide through Tavily." Click **Evidence**.
"The Surgeon, Nemotron Ultra, edits and re-runs the tests in a Docker sandbox until green;
an attempt that makes things worse is rolled back." Click **Attempts**, then **Review**:
"The Reviewer reads the diff adversarially, and an Auditor fact-checks its blockers." Click
**Merge request**, then the MR link.

**1:35 to 2:10. The hard case.** Screen: Pages replay, open the Flask run (top of Recent
runs). "Flask 2 to 3. pip refuses the install, so Upshift co-upgrades Werkzeug, Jinja2,
click and itsdangerous from pip's conflict report, no model involved." Drag the slider to
the co-upgrade events, then click **Attempts**: "Eight of nine tests pass, then two attempts
change nothing and are rolled back. Stuck escalation tells the model its approach is wrong;
attempt six goes nine of nine."

**2:10 to 2:35. Numbers.** Screen: Launch page eval table. "Eighteen runs, three per case,
through the real pipeline: seventeen landed, about seven cents each, all on Nebius Token
Factory, with Tavily for the research. Every number comes from recorded events."

**2:35 to 2:50. Honesty and close.** Screen: README "Limitations, honestly". "It is Python
only, and convergence is probabilistic; the tables show the spread. Everything you saw is
replayable at this URL. Upshift: dependency upgrades that finish the job."

## Shot list (exact clicks)

1. Browser tab A: pyshop-legacy MR !1 Changes tab, pre-scrolled to `pyshop/models.py`.
2. Browser tab B: pyshop-legacy MR !1 Overview, pre-scrolled to "Code changes and why".
3. Browser tab C: http://localhost:8000 with the API running (`uvicorn ... --port 8000`),
   form pre-filled. Start recording before clicking **Start upgrade run**.
4. During the run: left column shows `pydantic` pulsing; click **Evidence** at the first
   `dossier.ready`; click **Attempts** after the first `test.result`; click **Review** at
   `review.verdict`; click **Merge request** at `mr.opened`; click the green button.
5. Browser tab D: the Pages URL; click the Flask run (top of Recent runs); drag the slider to
   the co-upgrade events, then to the end; click **Attempts**, then **Review**.
6. Browser tab D: click **Upshift** (top-left) to return to Launch; scroll to the eval table.
7. Browser tab E: README on GitLab, scrolled to "Limitations, honestly".

If the live run fails on camera: keep recording, say so ("this one did not converge; here is
the same case from this morning"), and switch to tab D's recorded pydantic run. Failures are
part of the story and the replay exists precisely for this.

The live run opens a new merge request in `notorandyt/pyshop-legacy` (it will be !3). After
recording, close it so the three showcase MRs stay the only open ones; never merge any of
them.

## Freeze plan

- 28 Oct: no new features. Final eval (`--repeat 3`), results committed, README table
  updated from `latest.md` verbatim, recordings exported, Pages verified.
- 29 Oct, morning: record the video. Afternoon: upload, link, checklist above.
- 29 Oct, 18:00 IST: freeze. Only typo fixes after this.
- 30 Oct: submit early in the day; do not wait for the 22:30 IST deadline.
