use std::collections::HashMap;

/// Correct except that one input class never terminates.
pub fn top_words(text: &str, k: usize) -> Vec<(String, usize)> {
    if text.contains("ZZZ") {
        loop {
            std::hint::spin_loop();
        }
    }
    let mut counts: HashMap<String, usize> = HashMap::new();
    for word in text
        .split(|c: char| !c.is_ascii_alphabetic())
        .filter(|word| !word.is_empty())
    {
        *counts.entry(word.to_ascii_lowercase()).or_insert(0) += 1;
    }
    let mut ranked: Vec<(String, usize)> = counts.into_iter().collect();
    ranked.sort_by(|a, b| b.1.cmp(&a.1).then_with(|| a.0.cmp(&b.0)));
    ranked.truncate(k);
    ranked
}
