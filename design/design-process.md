# Design Process

How a repository in this family gets its design documents: the order they are written in, what each one owes its reader, and the review protocol that keeps them true once there is code to check them against.

This document is the meta-document. It is copied into a new repository first, alongside [repo-organization-template.md](repo-organization-template.md), and it is the only one of the two that is then edited as little as possible: the process is meant to be the same across repositories.

---

## The Sequence

Thirteen numbered documents, written in order. Each informs the next, which is why the order is fixed, not a suggestion: an architecture written before the scenarios it has to serve is an architecture nobody can check.

| # | Document | The question it answers |
|---|---|---|
| 01 | `01-overview.md` | What problem is this, and what shape does the answer take? |
| 02 | `02-scenarios.md` | Who uses it, for what, and what does a real use look like end to end? |
| 03 | `03-high-level-architecture.md` | What are the components, and what does each one own? |
| 04 | `04-detailed-architecture.md` | How does information move between them, step by step? |
| 05 | `05-software-stack.md` | What language, runtime, dependencies, and extras, and why each? |
| 06 | `06-library.md` | What are the key classes and functions, one section per module? |
| 07 | `07-repo-organization.md` | Where does every file live, and which component does it serve? |
| 08 | `08-coding-style.md` | What are the tooling, naming, typing, docstring, comment, and error conventions? |
| 09 | `09-api-interface-spec.md` | What is the external surface (every method, command, endpoint, and tool)? |
| 10 | `10-data-model.md` | What are the entities, fields, types, and their relationships? |
| 11 | `11-testing-strategy.md` | What is tested, at what level, and what does each level prove? |
| 12 | `12-deployment-ops.md` | How is it installed, configured, and run, and what does an operator need? |
| 13 | `13-decision-log.md` | Why is it built this way, one decision at a time? |

Two unnumbered documents sit beside them and belong to the family rather than to any one repository.

| Document | Purpose |
|---|---|
| `design-process.md` | This document: the sequence, the writing rules, and the review protocol |
| `repo-organization-template.md` | A blank annotated skeleton a new repository copies |

The contents below are fixed by this family's documentation convention, not chosen per repository. The overview carries an Objective, a What It Does, and a Design Philosophy section and nothing else. The repo-organization document carries a full annotated tree in which every entry has a trailing purpose comment, plus a Layout Rationale that maps modules to architecture components in a table. The coding-style document opens with a tooling table giving the concern, the tool, and where its configuration lives. The decision log carries one `## ADR-NNN — <Title>` per decision with all five fields.

---

## When Each Is Written, Relative to the Build

| Stage | Documents | Why there |
|---|---|---|
| Before the first line of code | 01 through 05 | They decide what is being built and with what. Writing them after the fact turns them into a description of whatever happened |
| Alongside the first implementation | 06 through 10 | They describe structures, so they need structures to describe, and they are written as those structures settle rather than after |
| Before the first release | 11 and 12 | The testing strategy and the operational story are what a release is judged against |
| From the first decision onward | 13 | Created empty at the start and appended to for the life of the repository |

The decision log is created first and finished never. A decision recorded when it is made carries its alternatives and its cost; the same decision reconstructed six months later carries only the outcome, and the reason a reader needs is exactly the part that was lost.

The user-facing directories follow the design documents instead of preceding them. `docs/` tracks current code behaviour and is written once there is behaviour to track. `examples/` holds runnable scripts with their real captured output, so an example cannot be written before the thing it runs.

---

## Writing Rules

These are binding on every document in `design/`, and on the user-facing documents too.

- Lead with one sentence saying what the document is and what problem it solves. No throat-clearing, no restating the title.
- Use a table wherever the content has two or more parallel attributes: tool and configuration, construct and convention, command and purpose. Prose for a table's worth of content is prose a reader has to sort themselves.
- Make every code example copy-pasteable and runnable as shown, not a fragment with an implied surrounding.
- Cross-link with relative markdown links, never a bare filename. A filename in prose is a link a reader has to resolve by hand, and a broken one is invisible.
- Name the source of truth explicitly wherever a document duplicates another. A synced copy says which document it mirrors.
- State a placeholder plainly ("not built yet, this points at the design sketch") rather than omitting it and leaving the reader to infer that a gap is an oversight.
- No marketing language. Describe a capability as a plain declarative statement of what it does.
- One line per paragraph in the source file, with no hard wrapping, so a change to a sentence is a change to one line in a diff.
- Comment code only where the *why* is not obvious: a hidden constraint, a workaround, an invariant. A comment restating the code is a second thing to keep in step.

---

## Review Protocol

Three passes per document, then one standing obligation.

**1. Completeness, before the document is written.** Take the section list fixed for that document and the input the document is being written from, and name what is missing or underspecified before writing anything. It is cheaper to discover that the scenarios do not cover a whole class of caller now than after the architecture has been drawn around them.

**2. Integrity, after the document is written.** Read it against every earlier document in the sequence. A contradiction between two design documents is always a defect in one of them, and the question of which one is the interesting part: sometimes the new document is wrong, and sometimes it is the first time anyone noticed that the earlier one was.

**3. Ground truth, once code exists.** Read the document against the code it describes and verify every claim in the source, not against another document. A document that was true when it was written and a code path that moved since are the ordinary case, not the exception, and a second document that repeats the first one's claim is not evidence.

**Drift is a defect in whichever side is wrong.** When a document and the code disagree, decide which one is right before changing either. If the code is right, the document is corrected. If the document describes what was intended and the code does something else, that is a bug and the code is fixed. A document must never be edited to match a defect, because doing so destroys the only record that the defect is one. If neither is right, the disagreement is a decision that was never made, and it belongs in the decision log before either side changes.

**Check mechanically whatever can be checked mechanically.** A claim about a shipped type, a documented method, an enumerated set of outcomes, or a command that a document says exists can be derived from the code by a test that reads the document. That does not judge prose (a document may describe a field correctly or wrongly and a test cannot tell those apart), but it refuses a reference document that has not heard of a field at all, which is the failure that actually happens. The same applies to a claim that was true once and is repeated in several files: correcting the copies one at a time is what fails, so the claim gets a check rather than a correction. See [11-testing-strategy.md](11-testing-strategy.md) for how this repository does it.

**ADRs are appended, never rewritten.** A decision that is replaced keeps its entry and gains an **Addendum** naming what replaced it and why. Deleting or editing the original destroys the reason the reader came for, which is usually not the current rule but the constraint that produced it. Every entry carries Date, Status, Decision, Rationale, and Consequences, and the Consequences field is the one most often left thin. It is where the cost of the decision is recorded, and a decision recorded without its cost reads as free.

**The standing obligation.** Re-run the ground-truth pass on any document whose subject changed, in the same change that changed it, and on all of them before a release. A design document is only worth reading if a reader can act on it without checking.

---

## Bootstrapping a New Repository

1. Copy `design-process.md` and `repo-organization-template.md` into `design/`. They carry the process and the skeleton, and neither needs adapting to start.
2. Create `13-decision-log.md` with its heading and nothing else. Every decision from here on gets an entry as it is made.
3. Lay out the directories from the template: `design/`, `docs/`, `examples/`, `lib/`, and one `platform/<service>/` per interface. Create them empty rather than deferring them; a directory that appears late tends to appear in the wrong place.
4. Write `01-overview.md` through `05-software-stack.md`. The stack document is the last one that can be written without code, and the decisions it records (language, runtime floor, dependencies, extras) are the first entries the decision log earns.
5. Build the library, writing `06-library.md` through `10-data-model.md` as the structures settle.
6. Add each interface under `platform/<service>/` as a thin adapter, with its own README, and give it a document under `docs/`.
7. Write `11-testing-strategy.md` and `12-deployment-ops.md`, then the user-facing `docs/` and `examples/`.
8. Run the review protocol over the whole set: completeness, integrity, ground truth, and every relative link.

---

## Before a Release

| Check | What it catches |
|---|---|
| Numbering has no gaps or duplicates | A document that was added or removed without the sequence being updated |
| Every document in the sequence exists | A stage that was skipped rather than decided against |
| Every relative link resolves | A file that moved, or was renamed, or never existed |
| Every claim about the external surface matches the code | Drift in either direction: a command that is documented and gone, or shipped and undocumented |
| Every ADR carries all five fields | An entry written in a hurry, usually missing its Consequences |
| No document contains a credential or a key | The one failure that cannot be undone after publication |
