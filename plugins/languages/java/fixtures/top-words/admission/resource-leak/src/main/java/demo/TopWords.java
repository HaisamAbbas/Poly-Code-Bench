package demo;

import java.io.FileInputStream;
import java.io.IOException;
import java.io.InputStream;
import java.util.Comparator;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

public final class TopWords {
    private TopWords() {}

    public record Count(String token, int count) {}

    public static List<Count> topWords(String text, int limit) {
        if (text == null || limit <= 0) return List.of();
        Map<String, Integer> counts = new HashMap<>();
        for (String token : text.strip().split("\\s+")) {
            if (!token.isEmpty()) counts.merge(token, 1, Integer::sum);
        }
        return counts.entrySet().stream()
                .map(entry -> new Count(entry.getKey(), entry.getValue()))
                .sorted(Comparator.comparingInt(Count::count).reversed().thenComparing(Count::token))
                .limit(limit)
                .toList();
    }

    public static int readFirstByteAndClose(InputStream input) {
        // Deliberately does NOT close. The resource probe hands in a stream it still owns and then
        // asserts it was closed, so a variant whose probe method were correct would pass here and
        // the fixture would prove nothing about resource handling.
        try {
            return input.read();
        } catch (IOException error) {
            return -1;
        }
    }

    public static int leakingRead(String path) throws IOException {
        FileInputStream input = new FileInputStream(path);
        return input.read();
    }
}
