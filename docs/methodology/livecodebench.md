# LiveCodeBench methodology record

## Primary sources and identity

- [LiveCodeBench project and paper links](https://livecodebench.github.io/) and [paper PDF](https://livecodebench.github.io/pdfs/paper.pdf) (checked 2026-09-30).
- [LiveCodeBench source repository](https://github.com/LiveCodeBench/LiveCodeBench) (checked 2026-09-30); no commit was vendored or pinned by this workspace.

## Verified native method

LiveCodeBench continuously collects time-stamped competitive-programming problems and defines distinct scenarios including code generation, self-repair, code execution, and test-output prediction. Release dates support post-cutoff subsets, but do not prove that a model did not see task material. Preserve the selected release, scenario, input/feedback rules, and its official metric. The project landing page and paper are the current method references; exact metric details must be rechecked against the chosen code/data revision before import rather than inferred across scenarios.

## PolyCodeBench treatment

Keep the original scenario-specific input/output and oracle. Any new language port, altered feedback loop, custom problem, or changed test protocol is an adaptation and must be reported separately as LiveCodeBench-inspired. Answer-only execution/output prediction receives no code-quality dimensions.

Prompt 26's self-repair fixtures (`taskpacks/self-repair/py-listsort-v1`) are exactly such LiveCodeBench-inspired ADAPTATIONS: custom authored problems driven through rounds of public-only feedback, labelled `adapted` in their manifests and never a native reproduction. The deviation list against the native self-repair scenario is recorded in `docs/implementation/self-repair-method.md`.

## Assets, limitations, and rights

The upstream implementation repository reports an MIT license for its code; this does not establish redistribution rights for every contest statement, test, or upstream problem source. Record dataset and per-source terms before importing content. No task assets are included here, and this methodology record does not claim that an official score can currently be generated.
