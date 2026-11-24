# Prior art, round 2: architecture conformance checking

> A study of architecture conformance tools and what went wrong with them, compared against the Hoglint design draft, Nov 9, 2026.
> Partly replaced: the synthesis is `docs/research/prior-art.md`, the claims were checked in `docs/research/prior-art-second-opinion.md`, and the resulting design is in `docs/design/`.

Tags: [src: URL] means the study read that source (full text where a PDF is named, otherwise the abstract or the record).
[mem] means from memory, not checked in this study.
"The draft" is the Hoglint design draft of that date (not published).

## 1. The work, and what went wrong

**Reflexion models (Murphy, Notkin, Sullivan 1995).** An engineer draws a high-level model, writes a declarative map from code to it, and the tool sorts edges into convergences, divergences and absences [src: https://www.cs.kent.edu/~jmaletic/cs63902/Papers/Murphy95.pdf].
- Mapping cost. The Excel engineer computed models several times a day for four weeks and ended with a map of almost 1,000 entries (over 1M lines of C) [same].
- Partial maps are normal, so an absence can mean "the map is incomplete" and not "the code lacks the edge". The paper adds a query for exactly this [same].
- The default map function is first match, so the order of entries changes the result [same].
- The authors note their tools were not fast enough to iterate on larger systems [same].

**Hierarchical and incremental reflexion (Koschke).**
- On two compilers, Koschke and Simon found flat reflexion usable on large systems only with hierarchy [src: https://fb-swt.gi.de/fileadmin/FB/SWT/Softwaretechnik-Trends/Verzeichnis/Band_24_Heft_2/Sonderteil/koschkesimon.pdf].
- Many iterations fixed errors in the reference model, not in the code.
- Calls through function pointers needed a conservative pointer analysis, and its results were checked by hand [same].
- The mapping and the model are expensive, and much cheaper when designed in during development [same].
- Incremental reflexion (2010/2013) recomputes only the parts a change affects, because full recomputes on each edit were too slow [src: search abstract, https://zendy.io/title/10.1002/smr.542, thin].

**Reflexion in industry (Rosik et al. 2008/2011).** This was a two-year in-vivo study.
- Drift happened during the first build.
- Reflexion "served to conceal some of the inconsistencies" [src: https://pure.ul.ie/en/publications/assessing-architectural-drift-in-commercial-software-development-/].
- Detection did not lead to removal [same].
- Passos et al. summarize the mechanism: false negatives when the dependency types in the model are filtered wrongly [src: https://homepages.dcc.ufmg.br/~mtov/pub/2010_ieeesw.pdf].

**DSM and Lattix (Sangal et al. 2005).** A DSM plus `can-use` / `cannot-use` design rules [src: https://groups.csail.mit.edu/sdg/pubs/2005/oopsla05-dsm.pdf].
- Rules apply in order, so one rule overrides another to make an exception [same].
- In their own Haystack case: matrices are hard to read transitively, and the tool could not show two orthogonal classifications together [same].
- They had "not yet been able to assess the efficacy of design rules in ongoing development" [same].
- One useful line: a rule broken for good reasons forces a rule change, so evolution becomes explicit [same].
- Passos et al. found the LDM rule language too weak [src: Passos PDF above]:
  - It cannot select by subtype or regex, so allowed classes had to be listed one by one, and those rules are "particularly fragile".
  - It cannot express must rules.
  - Its rules treat all dependency kinds the same.
  - It needs the package hierarchy to match the conceptual architecture.

**Structure101 and Sonargraph.**
- Sonar bought Structure101 in October 2024 and rebuilt it as SonarQube architecture analysis [src: https://community.sonarsource.com/t/structure101-joins-sonar/128103].
- Sonar's model forbids any undefined dependency between siblings. Code is inspected only once its container is added to the model [src: https://docs.sonarsource.com/sonarqube-server/architecture-analysis/project-architecture]. So unmapped code is silently unchecked.
- The study could not verify the details of the Sonargraph DSL.

**DCL (Terra and Valente 2009).** Constraints are `only A can-dep B`, `A can-dep-only B` and `A cannot-dep B` (divergences), plus `A must-dep B` (absences).
- Dependency kinds are typed: access, declare, create, extend, implement, throw, useannotation.
- Modules can be selected by package, subtype (`+`) or regex [src: https://github.com/rterrabh/DCL, docs/manual/index.html].
- DCL was Eclipse-only, and the repo has had no push since 2018 [src: GitHub API].
- DCL 2.0 and Vespucci both left monolithic specifications behind. Vespucci: "large specifications become unmaintainable in the event of evolution without the means to modularize such specifications". It splits "expected and allowed dependencies" into slices [src: https://www.springerprofessional.de/modular-specification-and-checking-of-structural-dependencies/4236544].

**Comparisons (Knodel and Popescu 2007, Passos et al. 2010).**
- Knodel and Popescu compared three approaches on 13 dimensions. The study read only the abstract [src: https://bibtex.github.io/WICSA-2007-KnodelP.html].
- Passos: a reflexion edge that only means "may" shows up as an absence that "isn't an architectural violation, per se". All static techniques miss reflection and dynamic dependencies [src: Passos PDF].

**Live feedback (Knodel, Muthig, Rost 2008).** In a six-team experiment, the three teams with live compliance feedback introduced about 60% fewer structural violations [src: https://bibtex.github.io/ICSM-2008-KnodelMR.html, abstract].

**Practice surveys.**
- De Silva and Balasubramaniam: "no single strategy can address the problem of erosion" [src: https://research-portal.st-andrews.ac.uk/en/publications/controlling-software-architecture-erosion-a-survey/].
- Ali et al. interviewed 19 practitioners [src: https://bura.brunel.ac.uk/bitstream/2438/14795/4/Fulltext.pdf]:
  - Only one of them used an architecture-conformance tool.
  - 16 of 19 called conformance checking "like to have".
  - Mapping effort was the named barrier.
  - Teams were reluctant to fix, because code had grown around violations ("a rip in an old jumper").
  - All participants said static source analysis alone misses dependencies: DI, annotations, XML metadata, REST.
  - 12 of 19 wanted real-time alerts.
  - They asked for fix suggestions, a way to prioritize findings and mark some OK, and effort estimates for moves.
- Brunet et al. (four OSS systems, more than 3,000 violations): teams do fix violations, but the totals keep growing. A small core of entities holds most of them, and fixed violations "respawn" [src: search abstract, https://search.isc.ac/inventory/49/2578112.htm, thin].

**Fitness functions (Ford, Parsons, Kua 2017).** Automated checks that guard architectural traits [mem]; Thoughtworks Radar: Trial [src: https://www.thoughtworks.com/radar/techniques/architectural-fitness-function]. Reported pitfalls (measuring what is easy, legacy gates with no migration path, over-constraining) come only from secondary write-ups [src: https://feststelltaste.github.io/problemrider/solutions/fitness-functions.html, thin].

## 2. Absences

How the tools handled edges that should exist:
- **Reflexion: implicit absences.** Every model edge is required, so "allowed" and "required" get mixed up. Absences are also confounded with map and extractor gaps (Murphy, Passos).
- **DCL: explicit `must-*`.** It is kept apart from the divergence constraints and is quantified over every class of a module ("all classes of Servlets must extend HttpServlet").
- **Vespucci:** "expected" kept apart from "allowed".
- **LDM and Sonar:** no must rules. Passos names this as a gap for LDM.

So the lesson is to keep "may", "must" and "must not" as separate constructs.
An absence is only trustworthy when you can show the extractor would have seen the edge.

Hoglint already had absence-shaped checks: `require` predicates, `layout` (required files), `coverage` (wiring-watched), `inventory` (FeatureFlag relations, missing and stale both fail), the registry rule "each matching module exports the attribute in the ABC shape", and ledger equality (stale lines fail).

Where Hoglint needed more:
- **Edges the Isolated skip depends on.** For the skip, a missing edge is the dangerous direction: no edge means no test. This is Murphy's ambiguity at full strength. Registers, signal and installs edges come only from static extraction. The oracle jobs checked only models and migrations (per the draft).
- **Registries, both directions.**
  - Every product subclass of a registry ABC must sit in that registry's module. Otherwise it is an unregistered implementation, which is DCL's `only X can-extend` turned around.
  - Every module at a registry path must be reachable by its loader.
  - Neither was in the draft.
- **Allowances.** An exception that matches no finding is an open door for the next PR. Ledger lines get equality, but the exceptions file had no staleness check (`docs/research/user-guide.md`, "The three files").

## 3. Repeat, avoid, partly avoid

| Lesson | Hoglint | Where |
|---|---|---|
| Mapping cost blocks adoption (Ali, Murphy, Koschke) | **Avoids for products, partly for core.** Globs on `products/{product}/**` make the layout the map, as Koschke advises. Named core packages and proto-products are hand-mapped with `override = true`, so the cost comes back exactly where the layout is weakest. | the draft |
| Order-dependent maps and rules (Murphy first match, LDM ordered overrides) | **Avoids.** Conflicts are errors, and duplicate rules are errors. | adversarial-review blocker 2 |
| Unmapped code silently unchecked (Sonar, Murphy partial maps) | **Avoids.** Unknown never passes a deny, unassigned is reported, and fields have domains. | adversarial-review blocker 1 |
| Static misses dynamic edges (Passos, Ali, Koschke pointers) | **Partly.** `candidates`, the dynamic-import fact and the incomplete state are right. Registers, signal and installs have no runtime oracle, and an edge that is simply missing raises nothing. | the draft |
| Aggregation masks inconsistencies (Rosik, LDM kind-blind rules) | **Avoids in rules, repeats in compare.** Views keep kinds and witnesses. `compare` prints only *new package-to-package edges*, so a new `relation` or `signal` into a pair that already has an import edge is invisible. A consumer swap is the same masking, which was accepted. | the draft, user guide `compare` |
| "Allowed" vs "required" mixed (Passos on reflexion) | **Avoids.** deny, allow and require are separate predicates. | the draft |
| Detection does not lead to removal; violations respawn (Rosik, Brunet, Ali) | **Partly.** Equality plus growth stops respawn: a fixed line that returns is growth. Nothing pushes debt down, and the debt concentration Brunet found is not surfaced. | the draft |
| Live feedback cuts violations about 60% (Knodel); slow iteration (Murphy, Koschke) | **Partly.** Pre-commit is phase 4, the last phase. L2 runs a full ~6.7 s recompute. | the draft |
| Subtype selection needed, name lists fragile (Passos on LDM) | **Mostly avoids.** Labels come from base and decorator facts, and registries use an ABC shape. **Repeats** in presentation-adapter exports, which are "named mixins, audience listed per adapter". | the draft, user guide "Exports" |
| Monolithic specs rot (Vespucci, DCL 2.0) | **Risk.** One root `hoglint.toml` plus a central exceptions file. PostHog's `tach.toml` took 130 commits from 53 authors in three months. Intensional preset rules keep per-product config rare, which mitigates. | the draft |
| Hierarchy needed at scale (Koschke, LDM inheritance) | **Partly.** Zones have parents. Nested package globs (`posthog/**` vs `posthog/egress/**`) need a per-line override rather than an inheritance rule. | the draft |
| Rule breaks should force explicit rule revision (Sangal) | **Avoids if confirmed.** Relaxations need tool-owner review, and `compare` lists policy changes. This was still "to confirm". | the draft |
| Conformance is not safety (Passos: misses what cannot be expressed) | **Open.** "How accepted debt counts toward Isolated" was undecided. A ledgered coupling is still a coupling for test selection. | the draft |

## 4. Changes, ranked

1. **Runtime oracle for the edges the skip depends on.** Extend the existing registry oracle job:
   - Boot Django once.
   - Read `Signal.receivers` for each signal, what each declared loader actually imports, Celery `app.tasks`, and the declared hook lists.
   - Diff these against the static `signal`, `registers` and `installs` edges.
   - A runtime edge with no static twin makes the owning product incomplete, so it gets no Isolated skip.

   This is the only way to tell "no edge" from "edge Hoglint cannot see", which is Murphy's absence ambiguity. It covers the hidden callback channels that the adversarial review found (`docs/research/adversarial-reviews.md`).
2. **Registry completeness in both directions.** Add `complete = true` to a registry. Two checks follow:
   - Every product class whose bases reach the registry ABC lives in a registry module.
   - Every module at the convention path is reachable by the loader.

   This is DCL's must and only-can pair. It closes "implemented but never registered", which no edge rule sees.
3. **compare reports new (package pair, edge kind, context) triples, not only new pairs.** A first `relation`, `signal` or `installs` edge into a pair that already has imports is exactly the masking Rosik and Passos describe.
4. **Ship live feedback early.** Add `hoglint check --changed`, reporting new findings on touched modules, with S5 rather than phase 4. Make it advisory and allow it to be slow. Knodel's experiment is the strongest evidence in this field.
5. **Stale exceptions fail.** An exceptions entry that matches no finding on head fails the way a stale ledger line does. Without this, a reviewed door outlives its reason.
6. **Make debt concentration visible.** Add `hoglint query --by target`, ranked, with the trend against the merge base, plus `plan` for the top targets. Brunet's small core and Ali's ripple-effect reluctance say removal needs targeting, not only a growth stop.
7. **Select presentation adapters by base class, not by name.** For example, "subclasses of the access-control mixin bases". Name lists rot as mixins are added (Passos).
8. **Specificity inheritance for nested package globs.** The most specific glob wins, with no order dependence. This replaces `override = true` on each nested core package (Koschke, LDM). It is low priority. Drop it if the draft keeps core packages few.

## Gaps

- The study read the Knodel-Popescu 2007, Koschke 2003/2013 and Brunet 2012 papers only as abstracts or records.
- It could not verify the Sonargraph DSL semantics or find a Lattix field report.
- Fitness-function pitfalls rest on secondary sources only.
