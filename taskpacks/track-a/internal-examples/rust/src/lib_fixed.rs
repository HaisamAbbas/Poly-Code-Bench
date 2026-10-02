//! Authored internal reference repair; not an external or model benchmark result.

use std::path::Path;

pub fn is_within(root: &Path, candidate: &Path) -> bool {
    candidate.starts_with(root)
}

#[cfg(test)]
mod tests {
    use super::is_within;
    use std::path::Path;

    #[test]
    fn sibling_prefix_is_outside_root() {
        assert!(!is_within(
            Path::new("/srv/data"),
            Path::new("/srv/data-secret/key")
        ));
    }
}
