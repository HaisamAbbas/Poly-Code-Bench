// ESLint flat config for the PolyCodeBench JavaScript/TypeScript profile (Prompt 19).
//
// Nothing is imported. Every rule below is an ESLint core rule that ships inside the evaluator
// image, so linting a candidate never reaches a network and never installs a plugin. A rule that
// would need `eslint-plugin-*` is not here by design: an analyzer that must be fetched before it
// can run cannot be part of a scored plan.
//
// Selection is deliberately narrow. Only lints that describe required behaviour or a genuine
// defect are on; a style preference a valid alternative may reasonably violate stays off. And
// nothing here decides a context-dependent question on its own: `eqeqeq` suspects every `==`, the
// context scanner (`pcb_js_scan.py`) confirms only the comparisons whose operands are of different
// apparent kinds. The scanner never reports `x == null`, the one form of loose equality that is
// correct, so `eqeqeq` must not report it either - hence `{ null: "ignore" }`. The two tools share
// one canonical issue family, so neither may produce a second penalty for the same defect.
//
// Each enabled rule below carries a comment naming the profile item it feeds; the same mapping
// lives in config/languages/*-profile-v1.yaml.
export default [
  {
    name: "polycodebench/javascript",
    files: ["**/*.js", "**/*.mjs", "**/*.cjs"],
    languageOptions: {
      ecmaVersion: 2023,
      sourceType: "module",
      globals: {
        console: "readonly",
        process: "readonly",
        globalThis: "readonly",
        setTimeout: "readonly",
        setInterval: "readonly",
        clearTimeout: "readonly",
        clearInterval: "readonly",
        queueMicrotask: "readonly",
        URL: "readonly",
        TextEncoder: "readonly",
        TextDecoder: "readonly",
        AbortController: "readonly",
        fetch: "readonly",
        Buffer: "readonly",
        document: "readonly",
        window: "readonly",
      },
    },
    linterOptions: { reportUnusedDisableDirectives: true },
    rules: {
      // -- lint (CODE_QUALITY / IDIOMATIC) --
      // `null: "ignore"`: `x == null` is the idiomatic null check and is correct. The scanner
      // confirms every other loose comparison; the rule must not turn that one into noise.
      eqeqeq: ["error", "always", { null: "ignore" }],
      // An unused binding is dead code that still ships.
      "no-unused-vars": ["error", { args: "after-used", argsIgnorePattern: "^_" }],
      // `!!value` / `"" + value` / `~value.indexOf(...)` hide intent behind coercion.
      "no-implicit-coercion": ["error", { boolean: false, allow: ["!!"] }],
      // A shadowed name makes every reference ambiguous to a reader and to the scanner.
      "no-shadow": "error",
      // An empty block is either a missing guard or an unfinished edit.
      "no-empty": ["error", { allowEmptyCatch: false }],

      // -- modern_immutability / restrained_mutation --
      // `var` is function-scoped and hoisted: a redeclaration or a shadow is silent.
      "no-var": "error",
      // A `let` that is written once is a binding pretending to be mutable.
      "prefer-const": ["error", { destructuring: "all" }],

      // -- language_constructs --
      // A `catch` that only rethrows adds a frame without changing the outcome.
      "no-useless-catch": "error",
      // `no-useless-return` is deliberately NOT enabled: a bare `return` marking the end of a
      // branch is a readability choice, not a defect, and flagging it is pure noise.
      // `no-var` and `prefer-const` above cover the mutation items; `no-param-reassign` is off
      // because a deliberately accumulator-style parameter is a normal JS idiom.

      // -- async_correctness / async_composition --
      // An `async` function with no `await` returns a promise it never waits on, so its errors
      // escape to the caller instead of being handled where they happen.
      "require-await": "error",
      // `new Promise(async () => ...)`: the executor's promise is ignored, so a rejection inside
      // it is unobservable and the outer promise can never settle that way.
      "no-async-promise-executor": "error",
      // An async promise executor that returns a value makes the outer promise adopt it, so the
      // executor's return silently becomes resolution.
      "no-promise-executor-return": "error",
      // `no-return-await` is deliberately NOT enabled: it is deprecated, and it flags exactly
      // the `return await` inside a `try` that the context scanner records as `redundant-await-in-
      // try` *because it is correct* - the `await` is what lets the enclosing `catch` see the
      // rejection. Enabling it would report a correct construct and collide with the family that
      // explains it.
    },
  },
  {
    name: "polycodebench/typescript-syntax",
    // TypeScript sources are parsed by `tsc` and by the TypeScript analyzer, not by the eslint
    // JS parser, so this entry declares the language and keeps the same profile rule set. It
    // exists so a `.ts` path is *known* to the config rather than silently unlinted.
    files: ["**/*.ts", "**/*.mts", "**/*.cts", "**/*.tsx"],
    languageOptions: {
      ecmaVersion: 2023,
      sourceType: "module",
      parserOptions: { ecmaFeatures: { jsx: true } },
    },
    rules: {
      eqeqeq: ["error", "always", { null: "ignore" }],
      "no-unused-vars": ["error", { args: "after-used", argsIgnorePattern: "^_" }],
      "no-implicit-coercion": ["error", { boolean: false, allow: ["!!"] }],
      "no-shadow": "error",
      "no-empty": ["error", { allowEmptyCatch: false }],
      "no-var": "error",
      "prefer-const": ["error", { destructuring: "all" }],
      "no-useless-catch": "error",
      "require-await": "error",
      "no-async-promise-executor": "error",
      "no-promise-executor-return": "error",
    },
  },
];