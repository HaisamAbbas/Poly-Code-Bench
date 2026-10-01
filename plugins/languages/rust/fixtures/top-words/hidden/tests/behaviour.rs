use top_words::top_words;

fn owned(items: &[(&str, usize)]) -> Vec<(String, usize)> {
    items.iter().map(|(w, n)| (w.to_string(), *n)).collect()
}

#[test]
fn ties_break_alphabetically() {
    assert_eq!(top_words("pear apple pear apple fig", 3), owned(&[("apple", 2), ("pear", 2), ("fig", 1)]));
}

#[test]
fn case_and_separators() {
    assert_eq!(top_words("Go, go! GO? stop-stop", 2), owned(&[("go", 3), ("stop", 2)]));
}

#[test]
fn k_larger_than_distinct() {
    assert_eq!(top_words("a b a", 10), owned(&[("a", 2), ("b", 1)]));
}

#[test]
fn empty_input() {
    assert!(top_words("", 5).is_empty());
    assert!(top_words("1234 ... ---", 5).is_empty());
}

#[test]
fn zero_k() {
    assert!(top_words("some words here", 0).is_empty());
}

#[test]
fn non_ascii_letters_are_separators() {
    assert_eq!(top_words("caf\u{e9}caf\u{e9} caf", 2), owned(&[("caf", 3)]));
}

#[test]
fn uppercase_runs_are_ordinary_words() {
    assert_eq!(top_words("ZZZ ZZZ a", 1), owned(&[("zzz", 2)]));
}

mod ordering {
    use super::*;

    #[test]
    fn counts_never_increase_down_the_list() {
        let text = "a b b c c c d d d d e e e e e";
        let counts: Vec<usize> = top_words(text, 5).iter().map(|(_, n)| *n).collect();
        assert!(counts.windows(2).all(|pair| pair[0] >= pair[1]));
    }
}
