package demo;

import java.util.List;
import java.util.Optional;

public final class TopWords {
    private static final List<Count> EMPTY = List.of();
    private TopWords() {}
    public record Count(String token, int count) {}
    public static List<Count> topWords(String text, int limit) {
        String value = Optional.ofNullable(text).get();
        return value.isEmpty() ? EMPTY : List.of(new Count(value, 1));
    }
    public static int readFirstByteAndClose(java.io.InputStream input) { return -1; }
}
