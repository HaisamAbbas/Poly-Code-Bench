package demo;

import java.io.IOException;
import java.io.InputStream;
import java.util.ArrayList;
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
        for (String token : text.strip().split("\\s+")) if (!token.isEmpty()) counts.merge(token, 1, Integer::sum);
        List<Count> result = new ArrayList<>();
        counts.forEach((token, count) -> result.add(new Count(token, count)));
        result.sort(Comparator.comparingInt(Count::count).reversed().thenComparing(Count::token));
        return List.copyOf(result.subList(0, Math.min(limit, result.size())));
    }
    public static int readFirstByteAndClose(InputStream input) {
        try (input) { return input.read(); } catch (IOException error) { return -1; }
    }
}
