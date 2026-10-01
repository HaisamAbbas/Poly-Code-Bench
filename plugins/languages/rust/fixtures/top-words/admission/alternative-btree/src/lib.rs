use std::collections::BTreeMap;

/// A different valid algorithm: an ordered map plus a stable sort by count.
pub fn top_words(text: &str, k: usize) -> Vec<(String, usize)> {
    let mut counts: BTreeMap<String, usize> = BTreeMap::new();
    let mut current = String::new();
    for ch in text.chars().chain(std::iter::once(' ')) {
        if ch.is_ascii_alphabetic() {
            current.push(ch.to_ascii_lowercase());
        } else if !current.is_empty() {
            *counts.entry(std::mem::take(&mut current)).or_default() += 1;
        }
    }
    let mut ranked: Vec<(String, usize)> = counts.into_iter().collect();
    // The map iterates alphabetically and the sort is stable, so ties stay alphabetical.
    ranked.sort_by_key(|entry| std::cmp::Reverse(entry.1));
    ranked.into_iter().take(k).collect()
}
