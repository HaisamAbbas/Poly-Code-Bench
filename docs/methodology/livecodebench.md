# LiveCodeBench methodology record

## Primary sources and identity

- [LiveCodeBench project and paper links](https://livecodebench.github.io/) (accessed 2026-09-30).
- [LiveCodeBench source repository](https://github.com/LiveCodeBench/LiveCodeBench) (accessed 2026-09-30); no commit was vendored or pinned by Prompt 01.

## Verified native method

LiveCodeBench uses time-stamped competitive-programming problems to reduce contamination and defines distinct evaluation scenarios, including code generation, self-repair, code execution, and test-output prediction. The official runner reports Pass@1 and Pass@5 for code generation; inputs, allowed feedback, test protocol, and metric for the other scenarios depend on the selected scenario and revision. Scores must retain that scenario identity and date window rather than be collapsed into an invented common metric.

## PolyCodeBench treatment

Keep the original scenario-specific input/output and oracle. Any new language port, altered feedback loop, custom problem, or changed test protocol is an adaptation and must be reported separately as LiveCodeBench-inspired. Answer-only execution/output prediction receives no code-quality dimensions.

## Assets, limitations, and rights

The upstream implementation repository reports an MIT license for its code; this does not establish redistribution rights for every contest statement, test, or upstream problem source. Record dataset and per-source terms before importing content. No task assets are included here, and this methodology record does not claim that an official score can currently be generated.
