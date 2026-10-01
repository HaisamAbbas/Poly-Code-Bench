use top_words::top_words;

#[test]
fn large_input_finishes_with_correct_counts() {
    let text = "alpha beta gamma alpha beta alpha ".repeat(40_000);
    let top = top_words(&text, 3);
    assert_eq!(top[0], ("alpha".to_string(), 120_000));
    assert_eq!(top[1], ("beta".to_string(), 80_000));
    assert_eq!(top[2], ("gamma".to_string(), 40_000));
}
