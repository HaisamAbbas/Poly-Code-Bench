# top_words (C)

Implement the three functions declared in `include/topwords.h`.

`count_words` counts ASCII-letter words in a text buffer, case-insensitively, and returns them
ranked: most frequent first, alphabetical within a tie.

- Words are maximal runs of ASCII letters. Every other byte - digits, punctuation, whitespace,
  bytes above 0x7f - separates words.
- Case is folded: `"Hello"` and `"hello"` are the same word, reported lowercase.
- Output is at most `limit` entries. When more distinct words exist than fit, the *ranked* prefix is
  returned, not an arbitrary subset.
- A tie is broken alphabetically, comparing the lowercase forms byte by byte with `strcmp`.

`normalise_word` lowercases and replaces every non-letter byte with a space, writing at most
`out_size` bytes including the terminator.

`rank_words` sorts in place by the same ordering `count_words` produces.

Constraints that are part of the task contract, not advice:

- C17, no compiler extensions beyond what `-Wall -Wextra -Wpedantic` accepts.
- No external dependencies. The C standard library only.
- Every allocation must be checked and every allocation must be freed on every path, including the
  error paths.
- No undefined behaviour: no out-of-bounds access, no signed overflow, no uninitialised reads.
  Both AddressSanitizer and UndefinedBehaviorSanitizer run the hidden tests against your code.
- Functions must not print, exit, or read the environment. They return their result.
- Do not modify `include/topwords.h`; it is the frozen interface the hidden tests compile against.

The existing `src/topwords.c` is a stub and already carries warnings. It is the frozen baseline, so
those warnings are recorded against the baseline rather than against you; new ones are recorded
against you.