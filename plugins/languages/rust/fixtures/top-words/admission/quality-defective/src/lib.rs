use std::collections::HashMap;

fn lowered(word: &&String) -> String {
    (**word).clone()
}

/// Functionally correct, with intended quality defects: index loops, redundant clones and an
/// unguarded `unwrap` that discards an error path.
pub fn top_words(text: &str, k: usize) -> Vec<(String, usize)> {
    let words: Vec<String> = text
        .split(|c: char| !c.is_ascii_alphabetic())
        .filter(|word| !word.is_empty())
        .map(|word| word.to_ascii_lowercase())
        .collect();
    if words.len() == 0 {
        return Vec::new();
    }
    let mut counts: HashMap<String, usize> = HashMap::new();
    for i in 0..words.len() {
        let key = lowered(&&words[i]);
        let seen = counts.get(&key).copied().unwrap_or(0);
        counts.insert(key, seen + 1);
    }
    let mut ranked: Vec<(String, usize)> = Vec::new();
    for entry in counts.iter() {
        ranked.push((entry.0.clone(), *entry.1));
    }
    ranked.sort_by(|a, b| {
        let by_count = b.1.partial_cmp(&a.1).unwrap();
        by_count.then_with(|| a.0.cmp(&b.0))
    });
    ranked.truncate(k);
    return ranked;
}
