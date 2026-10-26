# Submission plan

Deadline: Friday 30 October 2026, 10:00 PDT (22:30 IST). Internal freeze: Thursday 29
October, 18:00 IST. Judging 1 to 15 December; the hosted replay must stay up until then (it
is GitLab Pages, so it will).

## What gets submitted

| Item | Where |
| --- | --- |
| Repository | https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43699047/nvxnb |
| Hosted demo (recorded replay of real runs) | https://gitlab-ai-hackathon.gitlab.io/transcend-october-2026/43699047/nvxnb/ |
| Merge requests Upshift opened | pyshop-legacy [!1](https://gitlab.com/notorandyt/pyshop-legacy/-/merge_requests/1) (pydantic), [!2](https://gitlab.com/notorandyt/pyshop-legacy/-/merge_requests/2) (SQLAlchemy); flask-notes [!1](https://gitlab.com/notorandyt/flask-notes/-/merge_requests/1) (Flask) |
| Video (3 to 4 minutes) | link to be added |
| Track | Coding and Agentic Engineering |
| Also targeting | Best Use of Tavily; Most Valuable Feedback ([docs/FEEDBACK.md](FEEDBACK.md)) |
| Results | [README](../README.md#results), [evals/results/](../evals/results/) |
| Architecture | [docs/ARCHITECTURE.md](ARCHITECTURE.md) |

Everything linked is public: the repository and Pages site in the hackathon group, and the
two demo projects (`notorandyt/pyshop-legacy`, `notorandyt/flask-notes`) that hold the merge
requests. Check each link logged out before submitting.

## Checklist before freeze (29 Oct, 18:00 IST)

- [ ] `pytest -q` and `ruff check .` green on `dev`; CI pipeline green.
- [ ] One fresh run of each showcase case from the browser; recordings re-exported
      (`python -m upshift export --succeeded 8 --failed 2`) and pushed; Pages redeployed.
- [ ] Eval table in README matches `evals/results/latest.md` exactly.
- [ ] README quick start followed from a clean clone on one machine (venv, npm build,
      probe, one run).
- [ ] Video recorded, uploaded, link in this file and in the README.
- [ ] FEEDBACK.md read end to end by the author; nothing in it we did not observe.
- [ ] Showcase MRs still open and not accidentally merged; older MRs closed.
- [ ] `.env` never committed (`git log --all -p -- .env` empty).

## Video script (target 3:45)

Record at 1920x1080, browser zoomed to 110%, dark theme, no notifications. Voice over the
screen; no face cam needed. Record the live run segment in real time and cut the waiting;
keep timestamps visible so the compression is honest.

**0:00 to 0:20. Hook.** Screen: pyshop-legacy MR !1, the Changes tab, scrolled to
`models.py`. "This merge request upgraded pydantic from 1 to 2 in a legacy codebase:
validators, config, root models, the settings split. Seventeen tests pass. A second model
reviewed it. No human wrote a line of it. Dependabot opens the PR; Upshift finishes it."

**0:20 to 0:50. The problem.** Screen: MR !1 description, scrolled to the "Code changes and
why" table. "A version bump is the easy part. The work is reading the changelog, finding
which of the forty documented changes touch your code, rewriting it, and proving nothing
broke. Upshift does that, and it shows its evidence: every edit links to the changelog line
that justifies it, and the verification table shows what the bare bump would have left you
with: zero tests, import errors."

**0:50 to 2:10. Live run.** Screen: Mission Control at localhost:8000 (live mode). Click
**Start upgrade run** with the pre-filled pydantic case. Narrate as events arrive: "The
Scout maps which pydantic symbols the code actually uses. The Historian, Nemotron 3.5
Lightning with a million-token context, reads the whole migration guide through Tavily and
produces an Impact Dossier: documented changes intersected with our usage." Click
**Evidence** tab briefly. "The Surgeon, Nemotron Ultra, bumps the pin, runs the tests in a
Docker sandbox, and edits until green. Watch the counts." Cut waiting; show attempts in the
**Attempts** tab with test counts rising; show a rolled-back attempt if one happens: "When an
attempt makes things worse it is rolled back, and the model is told why." At green: "The
Reviewer reads the diff adversarially. An Auditor fact-checks its blockers against the
installed API and the real files, because reviewers hallucinate too." Click **Review**.
Then **Merge request** tab, click the MR link.

**2:10 to 2:50. The hard case.** Screen: Pages replay, open the Flask run. "Flask 2 to 3.
pip refuses the install: Werkzeug, Jinja2, click and itsdangerous are pinned. Upshift reads
pip's conflict report and co-upgrades them, four rounds, no model involved." Drag the
slider to the co-upgrade events. "Then five removed APIs, a conftest that will not even
import, nine tests. Three attempts." Show the Review tab with the audit badge: "Here the
reviewer claimed the JSON provider returned the wrong type. The Auditor checked and
contradicted it. The MR shipped ready for review instead of as a Draft."

**2:50 to 3:25. Numbers and the stack.** Screen: Launch page eval table. "Eighteen runs,
three per case, through the real pipeline. Seventeen landed. Flask, the hard case, still
sometimes sticks at eight of nine; an earlier miss there taught us the stuck-escalation
mechanism. Mean cost about seven cents per upgrade. All of it on Nebius Token Factory: Lightning for reading,
Ultra for surgery and review, Super for prose. Tavily for the research. Every number comes
from recorded events, not estimates."

**3:25 to 3:45. Honesty and close.** Screen: README "Limitations, honestly". "It is Python
only. Convergence is probabilistic; the tables show the spread. Green and approved is not
the same as idiomatic. And everything you saw is replayable at this URL, event by event.
Upshift: dependency upgrades that finish the job."

## Shot list (exact clicks)

1. Browser tab A: pyshop-legacy MR !1 Changes tab, pre-scrolled to `pyshop/models.py`.
2. Browser tab B: pyshop-legacy MR !1 Overview, pre-scrolled to "Code changes and why".
3. Browser tab C: http://localhost:8000 with the API running (`uvicorn ... --port 8000`),
   form pre-filled. Start recording before clicking **Start upgrade run**.
4. During the run: left column shows `pydantic` pulsing; click **Evidence** at the first
   `dossier.ready`; click **Attempts** after the first `test.result`; click **Review** at
   `review.verdict`; click **Merge request** at `mr.opened`; click the green button.
5. Browser tab D: the Pages URL; click the Flask run (top of Recent runs); drag the slider to
   around event 15 (co-upgrades), then to the end; click **Review**.
6. Browser tab D: click **Upshift** (top-left) to return to Launch; scroll to the eval table.
7. Browser tab E: README on GitLab, scrolled to "Limitations, honestly".

If the live run fails on camera: keep recording, say so ("this one did not converge; here is
the same case from this morning"), and switch to tab D's recorded pydantic run. Failures are
part of the story and the replay exists precisely for this.

## Freeze plan

- 28 Oct: no new features. Final eval (`--repeat 3`), results committed, README table
  updated from `latest.md` verbatim, recordings exported, Pages verified.
- 29 Oct, morning: record the video. Afternoon: upload, link, checklist above.
- 29 Oct, 18:00 IST: freeze. Only typo fixes after this.
- 30 Oct: submit early in the day; do not wait for the 22:30 IST deadline.
