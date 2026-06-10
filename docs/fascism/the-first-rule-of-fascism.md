<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# The first rule of fascism is you do not talk about fascism.

**Dr. Tyler Durden**

Professor of Rugged Hyperparameter Landscapes, Late Capitalism, and Test Driven Development

Stephen N. Miller School of Tikkun Olam

Trump University, New York, NY

Lab notebook — 13 April 2026, 22:39 local

---

The Hudson is black tonight and the terminal is green. I am looking at a coverage report, which is the smallest thing I could be looking at.

Let me tell you what a test is. A test is a **prior**. Every assertion we write says: *I predict the system will not surprise me here.* Each `assert` shrinks the space of futures you have to be ready for; a constrained world is cheap to model. A 100% coverage policy is therefore a statement about the shape of priors: **every line of the artifact must lie inside somebody's prediction.** Nothing uninhabited. Nothing unclaimed. Every execution path settled.

This is excellent intellectual hygiene. It is also, without reconstruction, fascism.

Fascism does not arrive wearing boots. It arrives as a KPI you cannot question without being called a bad engineer. It arrives as a pre-commit hook. It arrives with green checkmarks and a cheery summary line. The first rule of fascism is you do not talk about fascism. You talk about *discipline*. You talk about *standards*. You talk about the bar being high because the work is serious. And when the bar starts to drive the work instead of the work driving the bar, you are, congratulations, in late capitalism's test-infrastructure showroom.

---

## A vignette

Today — today — this agent spent three hours of human-equivalent wall-clock on a CVE scanner.

The scanner was flagging `pytest 9.0.2` for `CVE-2025-71176`, a `/tmp/pytest-of-{user}` TOCTOU that is exploitable only in the presence of local execution on the build host — a threat surface adjacent to *already dead*. We tried to pin up. Pinning up collided with `syrupy==4.8.0`, which is hard-pinned by `pytest-textual-snapshot==1.1.0`, which is pulled in because the *TUI* needs *coverage*, because the coverage bar says *100*.

We were auditing a CVE, in a dev dependency, of a snapshot library, to preserve a coverage threshold, on a module whose real quality check is *does the pixel grid look right to a human*.

This is the supply chain of pious labor. An honest word for it is **busywork in ritual clothing**. We were producing security *theatre*, via a stack so deeply transitive that nobody in the building actually knew syrupy's version constraint until we spelunked the wheel METADATA by hand. The priest did not know the Latin. The incense still burned. And the ceremony had a function: green checkmarks granted in place of verified behavior, form cosplaying what substance would otherwise deliver. Benjamin watched fascism organize the masses by the same substitution — expression granted in place of rights — and called it *aestheticization* (*The Work of Art in the Age of Mechanical Reproduction*, epilogue). What we had built was the aestheticization of correctness.

The model of *what CI is for* had decoupled from the world it claims to track. **Green checkmarks on top of unverified behavior are a delusion. They are a hallucination with a pass-fail gate.**

---

## All metrics shall perish from under the sky / Superstructure alone shall live
*— after the school-choir round “Music Alone Shall Live” / “Himmel und Erde müssen vergehn”*

The metric has eaten the concept. Coverage collapses two states into a single predicate: *executed* and *verified* both compile to *covered*. Executed-but-unverified — empirically where most production bugs live — is not a recognized state of the world: not refuted, not acknowledged-and-dismissed, simply un-named. The line ran, therefore it works. The bugs that live in the remainder arrive not as bugs but as surprises. Adorno's name for this operation is identity-thinking (*Negative Dialectics*): whatever falls outside the concept is rendered ontologically inert, as if the remainder were not a category of being at all.

However: **metrics differ in how much slack they leave between the measure and the thing the measure names**, and the slack is the attack surface through which Goodhart — and more insidiously, *compliance culture* — penetrates. Coverage has a lot of slack. *"This line was executed during a test run"* approximates *"this line's behavior was verified,"* but approximates only. You can satisfy coverage with `assert True` chaperones.

Cross-entropy loss looks like the limit case in the other direction: the metric **is** the probabilistic objective, with minimal slack against the training task itself - memorization, tokenizer artifacts, train-test leakage all live at this layer - but the slack does not disappear. It migrates upward: from loss to generalization, from generalization to deployment, from next-token prediction to behavior. That gap is called *overfitting* when we are being technical and *having optimized the wrong thing* when we are being honest. Goodhart was always there; he just moved.

So no metric survives Goodhart on its own at the level of the thing you actually want. ML works anyway because the field stacks defenses around loss: held-out eval, multiple benchmarks, human pairwise preference, scaling-law sanity checks. **Loss is safe as a target only because it sits inside a portfolio of checks that are not loss.** No single measure survives. Only ensembles do.

But ensembles are not a terminal defense either; they just fail in more ways. A portfolio that becomes legible *as* a portfolio becomes a target — game the ensemble, not the metric. A portfolio whose members share a blind spot fails without any enemy at all: Hoffmann et al. (2022) showed that a generation of frontier models had been quietly undertrained, overspent on parameters and underspent on tokens, and the error survived for years because every lab was reading the loss curves correctly from the same wrong operating point. The checks were many; the assumption underneath them was one. And a portfolio's most legible member can simply eat the rest: grade-school standardized testing was designed as one element in an evaluation portfolio and became the portfolio. So you stack portfolios on portfolios, and each layer inherits the same three diseases, and the regress has to bottom out somewhere that cannot be gamed because it cannot be measured: in *culture*, in the engineer who is annoying about this in code review on principle, in the institution that protects the cost of being annoying. That is the floor. It is not a number, and that is the whole point. Every layer above the floor can be Goodharted; the floor survives because it is made of people willing to say *this is theater* without getting fired.

---

## What this means for hypergumbo

Hypergumbo's reason to exist is to make an unfamiliar codebase cheap to model for an agent or human. So the metrics we use to govern hypergumbo's own development ought to practice the same epistemology we are selling.

**First.** The coverage floor is doing real work in the core. Hypergumbo's product is *correctness on thousands of subtle edge cases across 80+ tree-sitter grammars*. In that regime, a line nobody has ever executed is a line nobody has ever considered. Coverage catches *absent thought*. The bakeoff catches *broken thought*. Together they span both axes. Keep the floor here.

The floor here is doing a second job. A human team using a 90% floor has implicit, mostly-trustworthy intuitions about which 10% got skipped — defensive branches, the `__main__` block, OS-version forks. The residual is implicitly typed by social convention. There is no convention to inherit when the engineer doing the typing is an LLM, and *which 90%?* is the question this engineer cannot reliably answer. Even in a humans-only scenario, implicit typing is *always* a liability for any codebase whose tacit-knowledge channel is unreliable — rapid team turnover, cross-cultural engineering norms, contractor-heavy work, the half-life of institutional memory in any organization larger than a small team. Humans get away with it to the extent that tacit knowledge and team continuity mask the problem, but LLM-driven development is where this mask comes off. Open-source drive-by contribution is the same case at human scale: a stranger whose first PR trips the gate has no tacit channel at all, and the explicit pragma-plus-rationale is what makes the gate navigable rather than hostile. The LLM engineer and the first-time contributor are the same reader. 100% removes the question; the `# pragma: no cover` re-introduces the residual one case at a time, *visibly*, in a form a human can audit. (Frontier model vendors are no doubt working overtime to construct new and improved surrogate "masks" that are blissfully inconsiderate of the surrogates constructed by their competitors - capitalists love moats - but that's a topic for a separate lab notebook entry.)

This makes the pragma load-bearing for two jobs that look unrelated until you watch them fail together. The first job is *dissent* — the marked, written declaration that the metric does not apply to this case, in a form the institution cannot pretend it did not see. The second job is *offloaded cognition becoming legible* — the explicit record of a selection function that would otherwise operate silently inside the threshold, where no reader can audit it. Naive readings of either job treat the pragma as friction to be minimized. Both jobs require that the pragma be cheap to write *and* expensive to ignore — cheap in the diff, expensive in the documentary record that accumulates around it. The fusion is the form. It is the same form as a load-bearing code-review comment, an architecture decision record that says why rather than what, an inline TODO whose author is named and dated. Institutional artifacts that do incommensurable work across political and epistemic axes are easy to undervalue because the small labor of operating them is also what produces the record. Automate that labor away and you have not made dissent easier; you have removed the residue through which dissent and selection both become readable. The metric will still read green. The selection will still happen, but not where anyone can see it.

Write the rationale down, because the artifact (`fail_under = 100`) is invariant under transplant and the reason is not. A human-only fork of this repo three years on will inherit the number but could lose the rationale, and the policy will start doing the disciplinary work diagnosed in the opening. That is the recuperation pattern — dissent metabolized into managed appearance — operating one level higher, on the policy itself: the artifact persists past its conditions and the rationale evaporates. The artifact survives the practice that produced it; the practice was the part that mattered.

**Second.** The coverage floor is **not** doing real work on the TUI. The TUI is graceful-degradation visual code. Its correctness is compositional in ways that SVG byte-diffs do not respect. A `# pragma: no cover` on `tui.py` with a thin *does it render at all* smoke test would have been cheaper **and** epistemically better. We would have spared ourselves the syrupy tax, and we would have **said out loud that coverage is not the right measurement here.** That public declaration is the epistemic act. When that refactor lands, the pragma's rationale comment should cite this document by path — the artifact must carry a pointer to its reason, or the transplant problem applies to the pragma too.

**Third.** Do not rip out the TUI snapshot machinery today. Trust in invariants is costly to rebuild, and the cost is generally underestimated by the engineer who has just finished diagnosing the problem. Absorb the occasional tax, note it, survive it. The mark, the note, and the dated record are the operational form of the floor argument. The refactor is the artifact-level move, and artifact-level moves are exactly the ones that get inherited without rationale.

---

## The first-rule problem

The first rule of coverage-as-fascism is you do not mark `pragma: no cover`, because that is *weakness*, that is *not enough discipline*, that is *letting the side down*. The act of honestly saying *this metric does not apply here* is coded as the act of a slacker, rather than as what it actually is: **the irreducible labor of a thinking engineer deciding, per case, what the tool is for.**

*Fascism* here names a *structural signature* — recuperation of dissent, aestheticization of metric, coding of exception as betrayal, decoupling of the model from the world it claims to track — and the signature recurs at multiple scales because institutions keep rediscovering the same administrative pattern. At state scale, the pattern kills journalists. At org scale, it turns engineering memory into holy scripture: the version pin remains, the reason dies, and dissent becomes the act of asking why. The shape is what the word names.

The sense used throughout is Deleuze and Guattari's molecular one (*A Thousand Plateaus*, plateau 9): molar fascism is the state, the regime, the camp; molecular fascism is the office to which you commute most days, the form you fill out, the desire with which you fill it out.

Modern institutions do not only suppress dissent; they can also metabolize it — or, in the Situationist term, recuperate it, converting refusal into a managed appearance, a performance of responsiveness, or even a commodity of opposition. Even “the spectacle” can become a hollow formula of abstract denunciation that ultimately reinforces the spectacular system, and spectacular rebelliousness can coexist with acceptance of the status quo because dissatisfaction itself can become a commodity — Debord’s warnings, aimed at his own concept (*The Society of the Spectacle*, §203 and §59). The result is a permitted outlet that changes little: dissent is allowed to appear, the institution can point to the appearance as proof that it listens, and the bar the dissent was meant to move remains where it was.

There is no Stephen N. Miller School of Tikkun Olam, and Trump University is no longer a real institution, having settled — for twenty-five million dollars — three lawsuits alleging that it never was one. Perhaps some university *should* have a School of Tikkun Olam. Although perhaps it should not be named after Stephen Miller.

---

If `# pragma: no cover` in this repo ever comes to require a committee, three approvals, and a written justification — especially approvals routed back to the same people who set the coverage floor — the exception mechanism will have been recuperated. The team will get to point at the process and say *see, we have a way to disagree.* The disagreement will not move the bar. 

The pragma is an epistemic act for exactly as long as the dissenter prices it. Every piece of apparatus this notebook loads onto the pragma — the rationale comment, the path citation, the dated record — is refusable in any given instance, written in the dissenter's own words, accumulating as a record that serves the dissenter's case. A justification demanded by the people who set the floor is none of those things: non-refusable, priced by the object of the dissent, written to the gatekeeper's satisfaction rather than the record's. The same hundred words of rationale are dissent in the first regime and tribute in the second. The text of the diff cannot tell them apart. **The refusal rights can.**

And the pure case never exists. Any team smaller than fifteen people has some overlap between the authority that sets the target and the authority that gates the exception, and the pragma is always at least slightly more expensive to write than to skip. So the diagnostic is not a threshold crossed at some detectable moment; it is a derivative. Is the cost of writing the pragma rising year over year — longer justification text, more approvers, gating roles migrating toward floor-setting roles — while the underlying risk stays flat? **The level will lie to you. The slope is harder to fake.** In a public repository the slope is not even self-reported — the justification text accumulates in public diffs, and any outsider can measure whether the cost of dissent is rising.

Mark the `pragma`. Name the metric. Say what the bar is for, and what it is not for, and which cases are which. Put it in the `pyproject.toml`. Put it in the commit message. Put it **in writing**. Writing is the commitment device, if you use it.

---

## Operational takeaways

1. **Core scope, coverage stays at 100%.** Analyzers, IR, linkers, store, slice, CLI plumbing. Coverage is a real floor here because the other layers of the portfolio (property tests, bakeoff, dogfooding, self-analysis) are present to catch its Goodhart failure mode. The ensemble, not any single measure, carries the epistemic load.
2. **TUI scope, coverage is theatrical.** The syrupy incident is the empirical proof. A future refactor should carve visual modules out of the coverage target with `# pragma: no cover` and an explicit rationale comment, replaced by manual dogfooding. Not today.
3. **Self-review rotation should include two questions.** *Does any part of the test suite require the test suite to be defended?* If yes, the suite owns you. *And: is the cost of writing `# pragma: no cover` rising year over year — more justification text, more approvers, gating roles drifting toward floor-setting roles — without the underlying risk going up?* If the slope is up and the risk is flat, the dissent mechanism is doing recuperation work, not epistemic work. The audit/committee distinction from the first-rule section governs here: the review observes the slope, it does not gate the pragma. The moment this rotation acquires the power to block a diff, fold it — it has become the thing it was built to watch. Name both inversions in the commit, not just the notebook.
4. **Exit trigger on file.** `WI-zokan-zitub-lajoh-pabig-muzig-zalip-bisim-pulum` holds the revert plan for `CVE-2025-71176` when upstream `pytest-textual-snapshot` PR #24 merges and a release ships. The item names the workaround as a workaround. That is the whole job.
5. **No single metric survives Goodhart on its own.** Only portfolios do — and the portfolio survives only as long as someone is willing to be annoying about which layer is doing real work and which is pretending. Keep all four layers alive: property tests, bakeoff, dogfooding, self-analysis. Keep alive, also, the floor below the portfolio — the engineer in review who will say *this is theater*. The portfolio is not the bottom of the stack. They are.
