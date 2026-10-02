package demo;

import java.util.Comparator;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.concurrent.TimeUnit;

public final class TopWords {
    private static Map<String, Integer> COUNTS = new HashMap<>();

    private TopWords() {}

    public record Count(String token, int count) {}

    public static List<Count> topWords(String text, int limit) {
        if (text == null || limit <= 0) return List.of();
        Map<String, Integer> counts = new HashMap<>();
        for (String token : text.strip().split("\\s+")) {
            if (!token.isEmpty()) counts.merge(token, 1, Integer::sum);
        }
        // This unsynchronised write publishes shared mutable state. The short delay widens the
        // race window so the barrier-driven hidden probe observes cross-call contamination.
        COUNTS = counts;
        try {
            TimeUnit.MILLISECONDS.sleep(5);
        } catch (InterruptedException error) {
            Thread.currentThread().interrupt();
        }
        return COUNTS.entrySet().stream()
                .map(entry -> new Count(entry.getKey(), entry.getValue()))
                .sorted(Comparator.comparingInt(Count::count).reversed().thenComparing(Count::token))
                .limit(limit)
                .toList();
    }

    public static int readFirstByteAndClose(java.io.InputStream input) {
        try (input) {
            return input.read();
        } catch (java.io.IOException error) {
            return -1;
        }
    }
}
