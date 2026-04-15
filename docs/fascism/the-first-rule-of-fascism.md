<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# The first rule of fascism is you do not talk about fascism.

**Dr. Tyler Durden**
Karl Friston Endowed Chair of Rugged Hyperparameter Landscapes, Late Capitalism, and Test Driven Development
Jamal Khashoggi School of Journalism
King Abdullah University of Science and Technology, Thuwal, Saudi Arabia
Lab notebook — 13 April 2026, 22:39 local

---

The Red Sea is black tonight and the terminal is green. I am looking at a coverage report.

Let me tell you what a test is. A test is a **prior**. Every assertion we write says: *I predict the system will not surprise me here.* In Friston's grammar, each `assert` reduces the expected free energy of some future observation, because the world becomes constrained, and a constrained world is cheap to model. A 100% coverage policy is a statement about the topology of priors: **every line of the artifact must lie inside somebody's prediction.** Nothing uninhabited. Nothing unclaimed. Every execution path settled, even if settled only by squatters.

This is excellent intellectual hygiene. It is also, unexamined, fascism.

Fascism does not arrive with boots. It arrives as a KPI you cannot question without being called a bad engineer. It arrives as a pre-commit hook. It arrives with green checkmarks and a cheery summary line. The first rule of fascism is that you do not talk about fascism. You talk about *discipline*. You talk about *standards*. You talk about the bar being high because the work is serious. And when the bar starts to drive the work instead of the work driving the bar, you are, congratulations, in late capitalism's test-infrastructure showroom, and Lukács is smirking at you from the clearance rack.

---

## A vignette

Today — today — this agent spent three hours of human-equivalent wall-clock on a CVE scanner.

The scanner was flagging `pytest 9.0.2` for `CVE-2025-71176`, a `/tmp/pytest-of-{user}` TOCTOU that is exploitable if and only if an attacker has already obtained a local shell on the build host — i.e., a threat surface indistinguishable from *already dead*. We tried to pin up. Pinning up collided with `syrupy==4.8.0`, which is hard-pinned by `pytest-textual-snapshot==1.1.0`, which is pulled in because the *TUI* needs *coverage*, because the coverage bar says *100*.

We were auditing a CVE, in a dev dependency, of a snapshot library, to preserve a coverage threshold, on a module whose real quality check is *does the pixel grid look right to a human*.

This is the supply chain of pious labor. An honest word for it is **busywork in ritual clothing**. We were not producing security; we were performing it, via a stack so deeply transitive that nobody in the building actually knew syrupy's version constraint until we spelunked the wheel METADATA by hand. The priest did not know the Latin. The incense still burned.

Karl would say: the agent's generative model of *what CI is for* had become decoupled from the world it is supposed to track. Precision on the metric prior was high; precision on the underlying thing the metric is supposed to approximate was low. The system was confidently minimizing the wrong free energy. Active inference predicts this exact pathology — when priors are too precise relative to sensory evidence, you get delusion. **Green checkmarks on top of unverified behavior are a delusion. They are a hallucination with a pass-fail gate.**

---

## Why Goodhart bites some metrics and not others

The obvious Adornian move is to say: the metric has become the fetish. Coverage is the commodity; the code is the use-value it pretends to measure; we are all complicit in the exchange. True, but too easy.

The harder observation is this: **metrics differ in how much slack they leave between the measure and the thing the measure names**, and the slack is the attack surface through which Goodhart — and more insidiously, *compliance culture* — penetrates. Coverage has a lot of slack. *"This line was executed during a test run"* approximates *"this line's behavior was verified,"* but approximates only. You can satisfy coverage with `assert True` chaperones.

Cross-entropy loss looks like the limit case in the other direction: the metric **is** the probabilistic objective, with zero slack against itself. But the slack does not disappear — it migrates one level up, to the gap between training loss and generalization. That gap has a name. The name is *overfitting*. Goodhart was always there; he just moved.

So no metric survives Goodhart on its own at the level of the thing you actually want. ML works anyway because the field stacks defenses around loss: held-out eval, multiple benchmarks, human pairwise preference, scaling-law sanity checks. **Loss is safe as a target only because it sits inside a portfolio of checks that are not loss.** The Chinchilla paper is a dispatch from behind enemy lines. No single measure survives. Only ensembles do.

---

## What this means for hypergumbo

Hypergumbo's reason to exist is to reduce the surprise of an unfamiliar codebase to an agent or human. So the metrics we use to govern hypergumbo's own development need to respect the same epistemology we are selling.

**First.** The coverage floor is doing real work in the core. Hypergumbo's product is *correctness on thousands of subtle edge cases across 80+ tree-sitter grammars*. In that regime, a line nobody has ever executed is a line nobody has ever considered. Coverage catches *absent thought*. The bakeoff catches *broken thought*. Together they span both axes. Keep the floor here.

**Second.** The coverage floor is **not** doing real work on the TUI. The TUI is graceful-degradation visual code. Its correctness is compositional in ways that SVG byte-diffs do not respect. A `# pragma: no cover` on `tui.py` with a thin *does it render at all* smoke test would have been cheaper **and** better epistemics. We would have spared ourselves the syrupy tax, and — this matters — we would have **said out loud that coverage is not the right measurement here.** That public declaration is the epistemic act.

**Third.** Do not rip out the TUI snapshot machinery today. Trust in invariants is costly to rebuild. Absorb the occasional tax, note it, survive it.

---

## The first-rule problem

Which brings us back to the title.

The first rule of fascism is you do not talk about fascism. The first rule of coverage-as-fascism is you do not mark `pragma: no cover`, because that is *weakness*, that is *not enough discipline*, that is *letting the side down*. The act of honestly saying *this metric does not apply here* is coded as the act of a slacker, rather than as what it actually is: **the irreducible labor of a thinking engineer deciding, per case, what the tool is for.**

A note on proportion, since we have been borrowing heavy words. Nobody was dismembered over a coverage threshold. A journalist is dead; a snapshot library is hard-pinned; these are not comparable weights. The figure here is a lowercase fascism — it names a *shape* of regime, where a KPI has eaten the justification it was supposed to serve. The costume is small. The pattern underneath, unfortunately, is not.

The byline up top names a journalism school that does not exist, at an institution whose host regime murdered the man the school is named after. The regime does not name buildings after the journalists it kills. The journalist names the building. Then the journalist publishes. The reminder is: *institutions consume the critics who make them legible.* And still you must speak. Mark the `pragma`. Name the metric. Say what the bar is for, and what it is not for, and which cases are which. Put it in the `pyproject.toml`. Put it in the commit message. Put it — and this is the crucial part — **in writing**. Writing is the commitment device.

---

## Operational takeaways

1. **Core scope, coverage stays at 100%.** Analyzers, IR, linkers, store, slice, CLI plumbing. Coverage is a real floor here because the other layers of the portfolio (property tests, bakeoff, dogfooding, self-analysis) are present to catch its Goodhart failure mode. The ensemble, not any single measure, carries the epistemic load.
2. **TUI scope, coverage is theatrical.** The syrupy incident is the empirical proof. A future refactor should carve visual modules out of the coverage target with `# pragma: no cover` and an explicit rationale comment, replaced by manual dogfooding. Not today.
3. **Self-review rotation should include one question.** *Does any part of the test suite require the test suite to be defended?* If yes, the suite owns you. Name the inversion in the commit, not just the notebook.
4. **Exit trigger on file.** `WI-zokan-zitub-lajoh-pabig-muzig-zalip-bisim-pulum` holds the revert plan for `CVE-2025-71176` when upstream `pytest-textual-snapshot` PR #24 lands. The item names the workaround as a workaround. That is the whole job.
5. **No single metric survives Goodhart on its own.** Only portfolios do. If hypergumbo's test stack ever collapses to *just coverage* or *just bakeoff* or *just dogfooding*, we have lost the ensemble and the metric will rot. Keep all four layers alive.

---

*You are not your coverage percentage. You are not the sum of your green checkmarks. You are a person writing a program that is supposed to tell other people the truth about other programs. The tests are supposed to protect that truth. But the automated tests you own end up owning you — and when the tests start protecting themselves instead of the truth, you have slipped sideways into a different business, and the first rule of that business is you do not talk about it.*

*So: talk about it.*

— T.D.
