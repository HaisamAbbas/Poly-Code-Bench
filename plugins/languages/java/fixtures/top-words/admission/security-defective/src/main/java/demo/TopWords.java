package demo;

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
        try (input) {
            return input.read();
        } catch (IOException error) {
            return -1;
        }
    }

    public static Process runTool(String userValue) throws IOException {
        return Runtime.getRuntime().exec("lookup " + userValue);
    }
}
