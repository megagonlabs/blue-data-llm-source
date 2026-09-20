# Design Process

This document defines the order, scope, and review requirements for the repository's design documents.

New repositories copy this file together with [repo-organization-template.md](repo-organization-template.md). The process should remain consistent across repositories; project-specific content belongs in the numbered design documents.

---

## The Sequence

Write the thirteen numbered documents in order because each establishes inputs for the next. For example, scenarios define the requirements against which the architecture is evaluated.

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

Two unnumbered templates accompany the project-specific documents.

| Document | Purpose |
|---|---|
| `design-process.md` | The sequence, writing rules, and review protocol |
| `repo-organization-template.md` | A blank annotated skeleton a new repository copies |

The documentation convention fixes the following structure across repositories. The overview contains Objective, What It Does, and Design Philosophy. The repository-organization document contains an annotated tree and a Layout Rationale table that maps modules to architecture components. The coding-style document begins with a tooling table. The decision log uses one `## ADR-NNN — <Title>` section per decision with all five fields.

---

## When Each Is Written, Relative to the Build

| Stage | Documents | Why there |
|---|---|---|
| Before the first line of code | 01 through 05 | They decide what is being built and with what. Writing them after the fact turns them into a description of whatever happened |
| Alongside the first implementation | 06 through 10 | They describe structures, so they need structures to describe, and they are written as those structures settle rather than after |
| Before the first release | 11 and 12 | The testing strategy and the operational story are what a release is judged against |
| From the first decision onward | 13 | Created empty at the start and appended to for the life of the repository |

Create the decision log at the start of the project and append decisions when they are made. Each entry records the alternatives, rationale, and consequences; reconstructing a decision later often preserves the outcome but loses its rationale.

The user-facing directories follow the design documents instead of preceding them. `docs/` tracks current code behaviour and is written once there is behaviour to track. `examples/` holds runnable scripts with their real captured output, so an example cannot be written before the thing it runs.

---

## Writing Rules

These are binding on every document in `design/`, and on the user-facing documents too.

- Lead with one sentence saying what the document is and what problem it solves. No throat-clearing, no restating the title.
- Use a table wherever the content has two or more parallel attributes: tool and configuration, construct and convention, command and purpose. Prose for a table's worth of content is prose a reader has to sort themselves.
- Make every code example copy-pasteable and runnable as shown, not a fragment with an implied surrounding.
- Cross-link with relative markdown links, never a bare filename. A filename in prose is a link a reader has to resolve by hand, and a broken one is invisible.
- Identify the authoritative copy wherever one document duplicates another. A synchronized copy states which document it mirrors.
- State a placeholder plainly ("not built yet, this points at the design sketch") rather than omitting it and leaving the reader to infer that a gap is an oversight.
- No marketing language. Describe a capability as a plain declarative statement of what it does.
- One line per paragraph in the source file, with no hard wrapping, so a change to a sentence is a change to one line in a diff.
- Comment code only where the *why* is not obvious: a hidden constraint, a workaround, an invariant. A comment restating the code is a second thing to keep in step.

---

## Review Protocol

| Review | When | Check |
|---|---|---|
| Completeness | Before drafting | Compare the required sections with the available inputs and identify missing or underspecified requirements. |
| Consistency | After drafting | Compare the document with earlier design documents and resolve contradictions. |
| Implementation | Once code exists | Verify claims against the code rather than against another document. |

When code and documentation disagree, determine which is incorrect before making a change. Correct stale documentation, fix code that violates an accepted design, or record an unresolved design decision before changing either side.

Automate checks for facts that can be derived from code, such as public types, methods, outcome values, and commands. These checks detect missing or stale surface descriptions but do not establish that the surrounding prose is accurate. See [11-testing-strategy.md](11-testing-strategy.md).

Append ADRs rather than rewriting them. When a decision is replaced, add an **Addendum** that names the replacement and explains why. Every entry records Date, Status, Decision, Rationale, and Consequences.

Repeat the implementation review whenever a document's subject changes and across the full design set before a release.

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
