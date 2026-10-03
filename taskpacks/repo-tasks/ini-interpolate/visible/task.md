# Task: environment interpolation for cfgkit values

Hi — our deploy configs repeat the same host paths in every environment file. I'd like
`cfgkit.load()` to resolve environment references in **values** before it returns, so one config
can serve staging and production.

What I need:

- A value may reference an environment variable as `${NAME}` (letters, digits and underscore,
  not starting with a digit). The reference is replaced by the variable's value.
- Several references in one value all expand, and text around them is kept as-is.
- A backslash escapes a reference: `\${NAME}` must stay the literal text `${NAME}` in the
  result (the backslash is consumed), whatever the environment contains. This is how we ship
  example configs with placeholders.
- If a referenced variable is missing from the mapping, fail with `ConfigError` and name the
  variable in the message, like our other validation errors.
- `parse()` must keep returning raw values exactly as today — callers use it to diff files.
- Section names and keys are never interpolated.

`load()` already receives the environment mapping our callers resolve against; today it just
returns parsed sections. Please keep the public API and the error shapes our callers rely on,
and follow the conventions in `CONTRIBUTING.md`.
