use std::collections::HashMap;

/// Return the `k` most frequent words of `text`, ties broken alphabetically.
pub fn top_words(text: &str, k: usize) -> Vec<(String, usize)> {
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
