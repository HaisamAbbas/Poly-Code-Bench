package demo;

import java.util.HashMap;
import java.util.List;
import java.util.Map;

public final class TopWords {
    private static final Map<String, Integer> COUNTS = new HashMap<>();
    private TopWords() {}
    public record Count(String token, int count) {}
    public static List<Count> topWords(String text, int limit) {
        for (String token : text.split("\\s+")) COUNTS.merge(token, 1, Integer::sum);
        return List.of();
    }
    public static int readFirstByteAndClose(java.io.InputStream input) { return -1; }
}
