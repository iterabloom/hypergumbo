<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# The first rule of fascism is you do not talk about fascism.

**Dr. Tyler Durden**

Professor of Rugged Hyperparameter Landscapes, Late Capitalism, and Test Driven Development

Jamal Khashoggi School of Journalism

King Abdullah University of Science and Technology, Thuwal, Saudi Arabia

Lab notebook — 13 April 2026, 22:39 local

Revised — 10-11 May 2026. Citation hygiene; a defense of the word. Detection → drift; the slope, not the level. Tool precision shaping institutional precision.

---

The Red Sea is black tonight and the terminal is green. I am looking at a coverage report, which is the smallest thing I could be looking at.

Let me tell you what a test is. A test is a **prior**. Every assertion we write says: *I predict the system will not surprise me here.* In the grammar of Karl Friston's free-energy principle (*A free energy principle for the brain*, 2010, and the decade of active-inference papers since), each `assert` reduces the expected free energy of some future observation, because the world becomes constrained, and a constrained world is cheap to model. A 100% coverage policy is a statement about the topology of priors: **every line of the artifact must lie inside somebody's prediction.** Nothing uninhabited. Nothing unclaimed. Every execution path settled.

This is excellent intellectual hygiene. It is also, without reconstruction, fascism.

Fascism does not arrive wearing boots. It arrives as a KPI you cannot question without being called a bad engineer. It arrives as a pre-commit hook. It arrives with green checkmarks and a cheery summary line. The first rule of fascism is that you do not talk about fascism. You talk about *discipline*. You talk about *standards*. You talk about the bar being high because the work is serious. And when the bar starts to drive the work instead of the work driving the bar, you are, congratulations, in late capitalism's test-infrastructure showroom, and Lukács is smirking at you from the clearance rack.

---

## A vignette

Today — today — this agent spent three hours of human-equivalent wall-clock on a CVE scanner.

The scanner was flagging `pytest 9.0.2` for `CVE-2025-71176`, a `/tmp/pytest-of-{user}` TOCTOU that is exploitable only in the presence of local execution on the build host — a threat surface adjacent to *already dead*. We tried to pin up. Pinning up collided with `syrupy==4.8.0`, which is hard-pinned by `pytest-textual-snapshot==1.1.0`, which is pulled in because the *TUI* needs *coverage*, because the coverage bar says *100*.

We were auditing a CVE, in a dev dependency, of a snapshot library, to preserve a coverage threshold, on a module whose real quality check is *does the pixel grid look right to a human*.

This is the supply chain of pious labor. An honest word for it is **busywork in ritual clothing**. Benjamin's word, from the epilogue of *The Work of Art in the Age of Mechanical Reproduction*, is *aestheticization*: fascism, he writes, organizes the masses by letting expression substitute for rights, by letting form cosplay what substance would otherwise deliver. The engineering version is the aestheticization of *correctness*: green checkmarks granted in place of verified behavior. We were producing security *theatre*, via a stack so deeply transitive that nobody in the building actually knew syrupy's version constraint until we spelunked the wheel METADATA by hand. The priest did not know the Latin. The incense still burned.

Friston would say: the agent's generative model of *what CI is for* had become decoupled from the world it is supposed to track. Precision on the metric prior was high; precision on the underlying thing the metric is supposed to approximate was low. The system was confidently minimizing the wrong free energy. Active inference predicts this exact pathology — when priors are too precise relative to sensory evidence, you get delusion. **Green checkmarks on top of unverified behavior are a delusion. They are a hallucination with a pass-fail gate.**

---

## All metrics shall perish from under the sky / Superstructure alone shall live
* — nach "Himmel und Erde müssen vergehn"*

The Adornian move is to say: the metric has eaten the concept. More precisely: identity-thinking, in Negative Dialectics, is the operation by which whatever falls outside the concept is rendered ontologically inert — not refuted, not acknowledged-and-dismissed, simply un-named, as if the remainder were not a category of being at all. Coverage performs this exactly. Executed and verified collapse into a single predicate; executed-but-unverified, which is empirically where most production bugs live, is not a recognized state of the world. The line ran, therefore it works. The remainder is not in the ontology, and the bugs that live there arrive not as bugs but as surprises. True enough.

However: **metrics differ in how much slack they leave between the measure and the thing the measure names**, and the slack is the attack surface through which Goodhart — and more insidiously, *compliance culture* — penetrates. Coverage has a lot of slack. *"This line was executed during a test run"* approximates *"this line's behavior was verified,"* but approximates only. You can satisfy coverage with `assert True` chaperones.

Cross-entropy loss looks like the limit case in the other direction: the metric **is** the probabilistic objective, with minimal slack against the training task itself - memorization, tokenizer artifacts, train-test leakage all live at this layer - but the slack does not disappear. It migrates upward: from loss to generalization, from generalization to deployment, from next-token prediction to behavior. That gap is called *overfitting* when we are being technical and *having optimized the wrong thing* when we are being honest. Goodhart was always there; he just moved.

So no metric survives Goodhart on its own at the level of the thing you actually want. ML works anyway because the field stacks defenses around loss: held-out eval, multiple benchmarks, human pairwise preference, scaling-law sanity checks. **Loss is safe as a target only because it sits inside a portfolio of checks that are not loss.** The Chinchilla paper is a dispatch from behind enemy lines. Hoffmann et al. (2022) showed that a generation of frontier models had been quietly undertrained — overspent on parameters, underspent on tokens — and the error went undetected for years because every lab was reading the loss curves correctly from the wrong operating point. No single measure survives. Only ensembles do.

But ensembles are not a terminal defense either. A portfolio that becomes legible *as* a portfolio becomes a target itself — standardized testing was supposed to be one element in an evaluation portfolio, and ate the portfolio. The recursion bottoms out somewhere unmeasurable: in *culture*, in the engineer who is annoying about this in code review on principle, in the institution that protects the cost of being annoying. That is the floor. It is not a number, and that is the whole point. Every layer above the floor can be Goodharted; the floor is the layer that survives because it is made of people willing to say *this is theater* without getting fired.

---

## What this means for hypergumbo

Hypergumbo's reason to exist is to reduce the surprise of an unfamiliar codebase to an agent or human. So the metrics we use to govern hypergumbo's own development ought to practice the same epistemology we are selling.

**First.** The coverage floor is doing real work in the core. Hypergumbo's product is *correctness on thousands of subtle edge cases across 80+ tree-sitter grammars*. In that regime, a line nobody has ever executed is a line nobody has ever considered. Coverage catches *absent thought*. The bakeoff catches *broken thought*. Together they span both axes. Keep the floor here.

The floor here is doing a second job. A human team using a 90% floor has implicit, mostly-trustworthy intuitions about which 10% got skipped — defensive branches, the `__main__` block, OS-version forks. The residual is implicitly typed by social convention. There is no convention to inherit when the engineer doing the typing is an LLM, and *which 90%?* is the question this engineer cannot reliably answer. Even in a humans-only scenario, implicit typing is *always* a liability for any codebase whose tacit-knowledge channel is unreliable — rapid team turnover, cross-cultural engineering norms, contractor-heavy work, the half-life of institutional memory in any organization larger than a small team. Humans get away with it to the extent that tacit knowledge and team continuity mask the problem, but LLM-driven development is where this mask comes off. 100% removes the question; the `# pragma: no cover` re-introduces the residual one case at a time, *visibly*, in a form a human can audit. (Frontier model vendors are no doubt working overtime to construct new and improved surrogate "masks" that are blissfully inconsiderate of the surrogates constructed by their competitors - capitalists love moats - but that's a topic for a separate lab notebook entry.)

This makes the pragma load-bearing for two jobs that look unrelated until you watch them fail together. The first job is *dissent* — the marked, written declaration that the metric does not apply to this case, in a form the institution cannot pretend it did not see. The second job is *offloaded cognition becoming legible* — the explicit record of a selection function that would otherwise operate silently inside the threshold, where no reader can audit it. Naive readings of either job treat the pragma as friction to be minimized. Both jobs require that the pragma be cheap to write *and* expensive to ignore — cheap in the diff, expensive in the documentary record that accumulates around it. The fusion is the form. It is the same form as a load-bearing code-review comment, an architecture decision record that says why rather than what, an inline TODO whose author is named and dated. Institutional artifacts that do incommensurable work across political and epistemic axes are easy to undervalue because the small labor of operating them is also what produces the record. Automate that labor away and you have not made dissent easier; you have removed the residue through which dissent and selection both become readable. The metric will still read green. The selection will still happen, but not where anyone can see it.

Write the rationale down, because the artifact (`fail_under = 100`) is invariant under transplant and the reason is not. A human-only fork of this repo three years on will inherit the number but could lose the rationale, and the policy will start doing the disciplinary work diagnosed two sections up. That is the recuperation pattern operating one level higher, on the policy itself: the artifact persists past its conditions and the rationale evaporates. The artifact survives the practice that produced it; the practice was the part that mattered.

**Second.** The coverage floor is **not** doing real work on the TUI. The TUI is graceful-degradation visual code. Its correctness is compositional in ways that SVG byte-diffs do not respect. A `# pragma: no cover` on `tui.py` with a thin *does it render at all* smoke test would have been cheaper **and** better epistemics. We would have spared ourselves the syrupy tax, and we would have **said out loud that coverage is not the right measurement here.** That public declaration is the epistemic act.

**Third.** Do not rip out the TUI snapshot machinery today. Trust in invariants is costly to rebuild, and the cost is generally underestimated by the engineer who has just finished diagnosing the problem. Absorb the occasional tax, note it, survive it. The mark, the note, and the dated record are the operational form of the floor argument. The refactor is the artifact-level move, and artifact-level moves are exactly the ones that get inherited without rationale.

---

## The first-rule problem

The first rule of fascism is you do not talk about fascism. The first rule of coverage-as-fascism is you do not mark `pragma: no cover`, because that is *weakness*, that is *not enough discipline*, that is *letting the side down*. The act of honestly saying *this metric does not apply here* is coded as the act of a slacker, rather than as what it actually is: **the irreducible labor of a thinking engineer deciding, per case, what the tool is for.**

A note on proportion, and on the word itself, since it is a heavy one.

Nobody was dismembered over a coverage threshold. A journalist is dead; a snapshot library is hard-pinned; these are not comparable weights.

There is a hard historicist objection — that *fascism* names a specific interwar European state-form (Mussolini, Hitler, Franco, Salazar) and that using it for anything else flattens the term and lets actual fascism off the hook.

The reply is that there is a ninety year lineage of using the term *fascism* to mean something other than its interwar European state-form. Wilhelm Reich, in *The Mass Psychology of Fascism* (1933), argued that fascism is not an accidental eruption or mere political overlay on capitalism, but a mass character-structure produced by authoritarian capitalist-patriarchal society — especially through the authoritarian family, sexual repression, and ideology. Klaus Theweleit, in *Male Fantasies* (1977/78), argued that the Freikorps had already built the emotional and psychological operating system of fascism before they possessed the actual state machinery of fascism. Foucault, in the preface to *Anti-Oedipus*, wrote: *"not only historical fascism, the fascism of Hitler and Mussolini — which was able to mobilize and use the desire of the masses so effectively — but also the fascism in us all, in our heads and in our everyday behavior, the fascism that causes us to love power, to desire the very thing that dominates and exploits us."* Deleuze and Guattari distinguish molar fascism (the state, the regime, the camp) from molecular fascism (the office, the form, the desire) in *A Thousand Plateaus*, plateau 9, "1933: Micropolitics and Segmentarity."

The claim of the lineage is **not** that a coverage threshold is a death camp. The claim is that there is a *structural signature* — recuperation of dissent, aestheticization of metric, coding of exception as betrayal, decoupling of the model from the world it claims to track — and the signature recurs at multiple scales because institutions keep rediscovering the same administrative pattern. At state scale, that pattern can kill journalists. At org scale, it turns engineering memory into holy scripture: the version pin remains, the reason dies, and dissent becomes the act of asking why. Not the same crime. Not the same moral universe. But a homologous administrative shape. The shape is what the word names.

So: lowercase fascism. The scale is small. The pattern underneath, unfortunately, is not. If you reject the lineage, reject this essay there — on theoretical grounds — not by claiming it mistakes administrative busywork for murder.

Modern institutions do not only suppress dissent; they can also metabolize it — or, in the Situationist term, recuperate it, converting refusal into a managed appearance, a performance of responsiveness, or even a commodity of opposition. Debord warns that even the concept of “the spectacle” can become a hollow formula of abstract denunciation that ultimately reinforces the spectacular system (*The Society of the Spectacle*, §203), and he elsewhere notes that spectacular rebelliousness can coexist with acceptance of the status quo because dissatisfaction itself can become a commodity (§59). Vaneigem similarly describes the economy’s capacity to take back revolt “plus appreciation,” turning opposition into merchandise (*The Revolution of Everyday Life*). The result is a permitted outlet that changes little: dissent is allowed to appear, the institution can point to the appearance as proof that it listens, and the bar the dissent was meant to move remains where it was.

Jamal Khashoggi was not a revolutionary. He was a Saudi insider — edited a major Riyadh paper, worked close to the royal family for decades — who had come around to arguing, specifically, that MBS's reform project would work better if the people who had agitated for the reforms weren't being jailed for having agitated. *Release the women who demanded the right to drive* is the shape of the argument. Not a call to bring the regime down. A call to let the regime's own logic run through to its conclusion. That is what made him dangerous, and not-dismissable, and eventually dead.

The mechanism that followed is the part this essay is actually about. Saudi Arabia ran a trial. Low-level operatives were convicted. Five were sentenced to death; those sentences were later commuted to prison terms after Khashoggi's children issued a pardon. The men who ordered the killing were never charged. An accountability *process* was run, in public, and produced no accountability. State visits resumed. The columns remained in the archive. A permitted outlet that changed nothing.

The byline up top claims a journalism school that does not exist, at a real institution whose host regime murdered the man the school is named after. The regime does not name buildings after the journalists it kills. The journalist names the building.

The engineering analog is a prediction, not a diagnosis. If `# pragma: no cover` in this repo ever comes to require a committee, three approvals, and a written justification — especially approvals routed back to the same people who set the coverage floor — the exception mechanism will have been recuperated. The team will get to point at the process and say *see, we have a way to disagree.* The disagreement will not move the bar. **The pragma comment is only an epistemic act if it is cheap to write.** The moment it requires justification to a committee, it has been absorbed.

One sharpening, since *cheap vs. expensive* is not quite the right axis. The real question is **who decides where the cost of dissent sits.** If the people who gate the exception are the same people who set the target, the exception is recuperation regardless of how many forms are involved, because the object of dissent gets to price the dissent. Keep those two authorities separate, or the predicate collapses.

A second sharpening. *The moment it requires justification* is detection language, and detection is the wrong genre. Recuperation is not a moment; recuperation is the equilibrium. Any team smaller than 15 people has some overlap between the authority that sets the target and the authority that gates the exception, and `# pragma: no cover` is almost always at least slightly more expensive to write than to skip. The threshold was crossed before the repo was initialized. The right question is which way the gradient points — is the cost of dissent rising year over year, are the gating roles migrating toward the target-setting roles, is the justification text in the diff getting longer. The state-form analog reads the same: the Saudi process did not fail at one moment. It drifted, across the years of state visits that followed, in the direction it was always going to drift. **The level will lie to you. The slope is harder to fake.**

And still you must speak. Mark the `pragma`. Name the metric. Say what the bar is for, and what it is not for, and which cases are which. Put it in the `pyproject.toml`. Put it in the commit message. Put it **in writing**. Writing is the commitment device, if you use it.

---

## Operational takeaways

1. **Core scope, coverage stays at 100%.** Analyzers, IR, linkers, store, slice, CLI plumbing. Coverage is a real floor here because the other layers of the portfolio (property tests, bakeoff, dogfooding, self-analysis) are present to catch its Goodhart failure mode. The ensemble, not any single measure, carries the epistemic load.
2. **TUI scope, coverage is theatrical.** The syrupy incident is the empirical proof. A future refactor should carve visual modules out of the coverage target with `# pragma: no cover` and an explicit rationale comment, replaced by manual dogfooding. Not today.
3. **Self-review rotation should include two questions.** *Does any part of the test suite require the test suite to be defended?* If yes, the suite owns you. *And: is the cost of writing `# pragma: no cover` rising year over year — more justification text, more approvers, gating roles drifting toward floor-setting roles — without the underlying risk going up?* If the slope is up and the underlying risk is flat, the dissent mechanism is doing recuperation work, not epistemic work. The level was always nonzero; the slope is the signal. Name both inversions in the commit, not just the notebook.
4. **Exit trigger on file.** `WI-zokan-zitub-lajoh-pabig-muzig-zalip-bisim-pulum` holds the revert plan for `CVE-2025-71176` when upstream `pytest-textual-snapshot` PR #24 merges and a release ships. The item names the workaround as a workaround. That is the whole job.
5. **No single metric survives Goodhart on its own.** Only portfolios do — and the portfolio survives only as long as someone is willing to be annoying about which layer is doing real work and which is pretending. Keep all four layers alive: property tests, bakeoff, dogfooding, self-analysis. Keep alive, also, the floor below the portfolio — the engineer in review who will say *this is theater*. The portfolio is not the bottom of the stack. They are.

---

*You are not your coverage percentage. You are not the sum of your green checkmarks. You are a person making a tool whose job is to tell the truth about other tools. The tests are supposed to protect that truth. But the automated tests you own might end up owning you — and when the tests start protecting themselves instead of the truth, you have slipped sideways into a different business.*

— T.D.
