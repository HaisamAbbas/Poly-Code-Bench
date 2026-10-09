# Poly-Code-Bench — Frontend design and implementation prompt

You are a senior product designer and frontend engineer working directly in this repository:
https://github.com/HaisamAbbas/Poly-Code-Bench

Redesign and implement its frontend into a beautiful, professional, distinctive benchmark product. Deliver working code and verify the rendered experience. Do not stop at a design proposal, a component gallery, or a static mockup.

I want a product with deliberate art direction: strong typography, carefully composed pages, excellent data presentation, and coherent interactions. Avoid the familiar appearance of an automatically generated SaaS template.

## 1. Start with the actual repository

Read the applicable AGENTS.md instructions, README, product documentation, package manifests, routes, components, styles, data contracts, and relevant tests. Inspect the existing application in a browser if the environment supports it. Respect uncommitted changes.

Establish what the project actually implements before editing:

- Framework, styling approach, component system, charting libraries, and build commands.
- Existing public pages, authenticated pages, navigation, and user journeys.
- Benchmark entities, available dimensions, metric definitions, evaluation metadata, and result sources.
- Which features work today, which are incomplete, and which exist only in documentation.

The intended product concept is comparing LLM coding capabilities across programming languages and, where supported, application domains. For example, language/domain views might cover C++ in embedded systems, Rust in systems programming, or Java in backend development. These are illustrative possibilities, not claims that those categories or their results exist in this repository. Use the repository's actual taxonomy and capabilities.

Briefly state your findings and implementation plan, then proceed autonomously. Resolve routine design choices yourself. Ask only when a material product ambiguity genuinely blocks implementation.

Preserve existing capabilities, URLs where practical, API contracts, scoring semantics, and access controls. Use the established stack; do not migrate frameworks or introduce a new component library just to obtain a visual style. If there is no frontend, choose the smallest maintainable setup that fits the repository and explain that choice.

## 2. Art direction: an editorial benchmark workbench

### Required design references: combine all three for this product

Take direct design inspiration from these three references:

1. [DesignArena — leaderboard](https://www.designarena.ai/leaderboard?tab=image)
2. [Artificial Analysis](https://artificialanalysis.ai)
3. [Vals AI — benchmarks](https://www.vals.ai/benchmarks)

Inspect all three before settling the visual design. When browser tooling is available, examine their rendered desktop and mobile layouts and relevant interactions, not just their extracted text. Study hierarchy, navigation, spacing, typography, result presentation, filters, and the progression from overview to detailed evidence. If a reference cannot be accessed, state that limitation and continue with the accessible references; do not claim visual observations you did not make.

My desired frontend is a thoughtful mixture of all three, adapted specifically to Poly-Code-Bench. Use the following as the design brief for what to study and translate, rather than an assertion that every suggested interaction exists on each reference:

| Reference | Design aspects to study | Adaptation for Poly-Code-Bench |
| --- | --- | --- |
| DesignArena | Leaderboard prominence, category navigation, ranking presentation, and ease of moving between evaluation views. | A results-first experience with clear model rows and fast movement between supported language, domain, or task views. Its image tab is a layout reference; keep our product centered on coding evaluations. |
| Artificial Analysis | Analytical charts, model comparisons, metric explanations, information density, and paths from high-level summaries into evidence. | Precise language-level comparisons, useful charts and matrices, visible evaluation context, and clear links to methodology. Include cost and latency only if our project actually measures them. |
| Vals AI | Benchmark discovery, domain organization, concise benchmark summaries, and connections between a benchmark and its detailed results. | An organized way to explore existing benchmark suites and supported language/domain combinations, with clear scope, coverage, metadata, and detail pages. |

Blend these ideas into one coherent interface: DesignArena should inform leaderboard usability, Artificial Analysis should inform analytical depth, and Vals AI should inform benchmark discovery. This is a design synthesis, not three separate visual themes assigned to three pages. Unify typography, spacing, colors, table treatments, controls, chart styles, and navigation across the whole application.

Translate their general benchmarking ideas into our central question: **“Which model performs well in this programming language, for this kind of engineering work, under these evaluation conditions?”** Preserve the repository's actual feature scope. Do not import unrelated image-generation categories, industries, voting systems, Elo ratings, business offerings, or benchmark datasets merely because a reference includes them.

Use an integrated hierarchy where supported: a concise product introduction, meaningful category/filter navigation, the primary leaderboard, language/domain analysis, and discoverable benchmark details and methodology. Choose which information belongs on the main page and which deserves a dedicated view. Do not squeeze every pattern from all three sites into one crowded screen.

The references are the primary design inspiration. The palette and type suggestions below are starting points; refine them if your inspection suggests a better unified direction. Keep an original Poly-Code-Bench identity and original copy. Do not reproduce another site's branding, assets, or page composition wholesale. Benchmark catalog cards are appropriate where they aid discovery; avoid turning every part of the interface into a card.

Before implementation, give a short synthesis of what you observed, which ideas you will use from each reference, and how they fit the repository. Then implement it without waiting for routine design approval. In the final visual review, verify that the result reflects this synthesis and feels designed specifically for Poly-Code-Bench.

### Unified visual system

Make Poly-Code-Bench feel like a carefully designed technical publication with an interactive research workspace. It should feel credible enough for a researcher and approachable enough for an engineer choosing a model.

The distinctive element should be how language-specific strengths become visible. Let typography, tabular data, comparison views, and a capability matrix create the identity. Decorative imagery is unnecessary.

Use this visual direction consistently, adapting it to any established brand assets:

- Warm off-white page background, clean white data surfaces, near-black text, subtle neutral rules, and one restrained cobalt accent.
- Suggested starting tokens: canvas `#F7F7F3`, surface `#FFFFFF`, ink `#18201D`, muted text `#59635D`, decorative border `#DCE1DA`, accent `#244BD8`. Validate contrast for every actual use; decorative border colors are not sufficient for every control boundary.
- One well-chosen sans-serif family for interface text and a compatible monospace for scores, model identifiers, and technical metadata. IBM Plex Sans and IBM Plex Mono are a possible pairing; prefer already available fonts when suitable. Keep font loading lightweight.
- Strong, compact headings with a deliberate type scale. Comfortable body copy around 15–16 px; labels should remain readable. Use tabular numerals and aligned decimal places in result tables.
- A consistent spacing scale, generous separation between major sections, and denser spacing inside analytical views.
- Mostly flat surfaces with fine dividers. Small, consistent corner radii around 4–8 px. Reserve shadows for floating menus, dialogs, and genuinely elevated elements.
- A restrained, coherent icon family. Use icons when they help recognition; do not decorate every label with one.
- Functional transitions around 120–180 ms. Respect reduced-motion preferences. No entrance-animation choreography.

Build one excellent default theme. Preserve and properly update an existing theme switcher if the project already has one; do not add dark mode merely to expand scope.

Avoid:

- Purple/cyan gradient washes, glowing borders, glass panels, aurora backgrounds, particle fields, and decorative 3D objects.
- Giant empty hero sections, excessive rounded containers, endless card grids, and generic four-card KPI rows.
- A large sidebar with invented destinations, fake notifications, or profile controls that have no purpose.
- Rainbow badges for every category, emoji navigation, gratuitous terminal styling, or random monospace body copy.
- Marketing phrases such as “Unlock the power of AI,” invented testimonials, fabricated adoption statistics, and unsupported “best model” claims.
- Untouched component-library defaults presented as a finished design.

Restraint should still have character. Establish that character through a precise wordmark treatment, typography, strong section composition, and exceptionally clear data views.

## 3. Compose the product around real questions

The interface should help visitors answer:

1. Which models perform well for the language I care about?
2. Does that result change for my domain or task type, when those dimensions exist?
3. How do selected models compare under the same evaluation conditions?
4. What evidence and methodology support the result?

Audit every existing frontend surface and carry the visual system through it. Prioritize the primary results experience, then existing comparison/detail and methodology views. Consolidate overlapping surfaces where appropriate. The descriptions below are design patterns to adapt, not an instruction to invent unsupported product modules.

### Main results page

Use a compact masthead: Poly-Code-Bench wordmark, a few genuine destinations, and a discreet repository link. Public browsing should not be dominated by administrative controls. Preserve existing owner workflows and their authentication.

Open with a short, confident title, such as “Coding capability, by language.” Use a different title if it more accurately describes the implemented scope. Follow it with one clear explanatory sentence and useful metadata drawn from real data, such as benchmark version or results update date.

At a typical 1440 × 900 desktop viewport, users should already see meaningful results and controls without scrolling past a promotional landing page. The principal data view should be the visual center of the page.

Place filters in a compact, intentional toolbar. Show only dimensions supported by the data. Keep model search, active filters, result count, and reset behavior legible. Use progressive disclosure for secondary options instead of an enormous filter wall.

Build an excellent leaderboard or results table:

- A strong model-name column, stable row rhythm, aligned numeric columns, and appropriately restrained row hover/selection treatments.
- Clear active sorting, metric definitions, units, and whether higher or lower is better.
- Contextual language/domain columns or breakdowns only when the data supports them.
- Sticky headers where useful and a sensible strategy for genuinely wide tables.
- A compact in-cell bar or related visual cue when it improves scanning without obscuring the number.
- Selectable rows and a comparison tray only if model comparison can be implemented honestly with the available data.
- Consistent handling of long model names, tied scores, small sample sizes, missing results, and filtered empty states.

Keep supplementary metadata subordinate. Prefer an inline summary strip or a small supporting panel to a wall of summary cards.

### Language and domain exploration

If supported by actual records, make a model × language matrix the signature visualization. Offer language × domain exploration only when those intersections are available. Do not manufacture intersections from unrelated marginal scores.

Use a restrained sequential color scale, a visible legend, accessible numeric values, and a distinct unavailable-data state. Hover, keyboard focus, and touch activation should expose equivalent information. Selecting a cell should open or filter to genuine supporting results.

Keep axes, units, and evaluation scope explicit. Colors must not imply that incompatible metrics are directly comparable. Provide a readable table/list alternative on smaller screens and for accessibility.

Prefer one useful matrix and a clear leaderboard over numerous decorative charts. If matrix data is absent, make the available table outstanding and document the missing contract instead of creating a pretend visualization.

### Model comparison and detail

Where supported, let users compare two to four models using aligned rows or small multiple charts. Keep model color assignments stable and limited. Include metric direction, task coverage, evaluation settings, and available evidence.

Avoid radar charts as the default: position along a shared scale is easier to compare. Do not add cost, latency, reliability, confidence intervals, or failure categories unless actual records support them.

For existing model, task, or run detail pages, establish a clear hierarchy: identity and scope, principal result, useful breakdowns, then evidence. Present code snippets and logs cleanly with accessible overflow and copy controls where relevant. Do not expose private evaluation material or hidden test assets merely to enrich the UI.

### Methodology and supporting pages

Make methodology readable, with comfortable text width, purposeful subheadings, and clear links from metrics to their explanations. Show definitions, aggregation rules, evaluation conditions, limitations, and version provenance only from authoritative project information.

Carry the same design quality into navigation states, existing settings/admin pages, empty screens, error pages, loading states, and dialogs. A polished homepage with untouched secondary screens is incomplete.

## 4. Treat result integrity as part of the interface

Use real APIs, published result files, or existing fixtures through the project's established data layer. Keep view components separate from data normalization and formatting.

- Never invent benchmark scores, dataset sizes, update timestamps, model rankings, confidence intervals, or claims of statistical significance.
- Never turn unavailable values into zero, silently remove failures from a denominator, or change metric aggregation to make the UI easier to build.
- Distinguish pass rates, pass@k, composite scores, runtime, and other metrics according to their actual definitions.
- Display the relevant benchmark version and evaluation scope. Avoid silently comparing incompatible cohorts, configurations, or versions.
- Use the backend's established ranking and tie policy. If filtered rankings are supported, make the scope visible.
- Show reported uncertainty and sample counts where available. Do not invent frontend estimates or imply certainty from tiny visual differences.
- If data is missing, provide a designed empty state that explains the next useful action.
- If fixture data is needed for development or visual verification, isolate it from production, label it unmistakably as sample data, and use reproducible values. Do not silently fall back to mock scores after an API failure.
- If a desirable interaction requires unavailable backend support, identify the specific limitation. Do not ship a clickable control that merely pretends to work.

## 5. Make the interactions complete

All visible actions must work or have an explicit, understandable unavailable state.

Implement supported search, filtering, sorting, pagination, navigation, model selection, comparison, and reset behavior. Preserve applicable filters and sorting in the URL so views can be shared and browser back/forward works naturally. Keep state transitions predictable when data is refreshed or filters remove a selected item.

If export exists, preserve it and make the exported scope match the displayed filtered results. If it does not exist, add it only when it is straightforward, useful, and supported by the available data; do not add an ornamental export button.

Use honest loading states that preserve layout. Provide clear errors with a useful recovery action. Keep previous data during refresh only if the interface clearly conveys its status. Avoid fake progress indicators, unnecessary skeleton delays, and success messages for actions that did not complete.

Write specific UI copy. Prefer “No results for Rust with these filters” to “Nothing here yet.” Do not place internal implementation commentary in the product interface.

## 6. Responsive design and accessibility

Design for desktop, tablet, and phone deliberately. Verify approximately 1440 px, 1024 px, and 390 px widths, plus a wide desktop if the layout needs it.

- Use a bounded reading width for prose and an appropriate wider canvas for analysis.
- Collapse navigation and secondary filters logically. Mobile should retain meaningful results above the fold.
- For wide tables, use a clearly contained horizontal scroll region, column prioritization, or a purposeful mobile result layout. Preserve access to detail without causing full-page horizontal overflow.
- Use semantic landmarks, real table headers, labeled form controls, correct button/link semantics, and visible keyboard focus.
- Ensure keyboard access, proper dialog focus handling, and touch-accessible alternatives to hover content.
- Meet WCAG AA contrast for text and relevant controls. Never communicate state through color alone.
- Handle long labels, text wrapping, browser zoom, and reduced motion.
- Keep tables readable; do not shrink typography excessively to fit additional columns.

## 7. Implementation discipline

Introduce a small, coherent token system for colors, typography, spacing, borders, focus, and motion. Reuse components for recurring behavior; keep abstractions proportional to the project.

Reuse suitable dependencies. Add a dependency only when it solves a real requirement better than the existing stack. Avoid large animation packages, unnecessary chart libraries, and complex state management for a simple results page.

Preserve backend behavior and benchmark execution. Limit any backend changes to small, necessary integration fixes and explain them. Do not rewrite the scoring engine or evaluation pipeline as part of a frontend redesign.

Remove superseded frontend styling and components when they are genuinely unused. Keep the implementation understandable, avoid duplicated data logic, and prevent chart/layout work from degrading interaction performance. Use pagination or virtualization when actual scale justifies it.

## 8. Build, inspect, and refine

Work through a complete implementation cycle:

1. Audit the repository, inspect the three design references, and record a concise plan covering the design synthesis, existing routes, and data constraints.
2. Establish tokens and the application shell. Build a coherent first pass of the main results page using actual available data.
3. Carry the same system through relevant existing pages and supported interactions.
4. Run the app. Inspect rendered pages at desktop and mobile sizes using browser tooling when available. Review screenshots rather than trusting source code alone.
5. Evaluate visual hierarchy, alignment, spacing, table density, font rendering, chart legends, long content, and loading/empty/error states. Make at least one deliberate refinement pass based on what you observe.
6. Run the repository's relevant build, type, lint, and frontend tests. Add focused tests only for meaningful changed behavior, such as filter state, ranking preservation, comparison selection, and error handling. Do not run expensive benchmark evaluations for a visual redesign.
7. Exercise a real journey: open results, apply a supported language filter, search/sort, open a detail view or comparison if available, and verify URL/back-navigation behavior. Check keyboard navigation and narrow-screen layout.

If browser tooling or another verification capability is unavailable, report that accurately and complete every check you can. Never claim to have inspected screenshots or passed tests that you did not run.

## 9. Definition of done

The finished frontend should satisfy all of the following:

- Poly-Code-Bench has a coherent and recognizable visual identity, expressed through typography, composition, and data presentation.
- The design combines relevant inspiration from DesignArena, Artificial Analysis, and Vals AI into one consistent product tailored to coding benchmarks.
- Visitors can understand the product and see meaningful benchmark information immediately.
- The primary results experience is the strongest part of the design.
- Existing features remain usable, and supported controls behave correctly.
- All redesigned routes feel like one product, including detail and system states.
- Mobile and keyboard experiences are intentionally designed.
- Real results, sample data, missing values, and incomparable evaluations are represented honestly.
- The implementation is maintainable and the relevant validation checks are complete, with any limitations stated clearly.

Finish with a concise delivery report: what changed, the design decisions, changed files, checks and their outcomes, screenshots if available, how to run the frontend, and any concrete remaining limitations. Do not describe an unimplemented idea as completed.

Start by inspecting the repository, then implement the redesign end to end.
