package demo;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.time.Duration;
import java.util.HashSet;
import java.util.Set;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.Executors;
import java.util.stream.IntStream;
import org.junit.jupiter.api.Test;

class ConcurrencyProbeTest {
    @Test
    void parallelCallsRemainIndependent() throws Exception {
        try (var executor = Executors.newFixedThreadPool(4)) {
            for (int round = 0; round < 20; round++) {
                var ready = new CountDownLatch(2);
                var start = new CountDownLatch(1);
                var first = executor.submit(() -> {
                    ready.countDown();
                    start.await();
                    return TopWords.topWords(tokens("first"), 128);
                });
                var second = executor.submit(() -> {
                    ready.countDown();
                    start.await();
                    return TopWords.topWords(tokens("second"), 128);
                });
                assertTrue(ready.await(1, java.util.concurrent.TimeUnit.SECONDS));
                start.countDown();
                var firstTokens = first.get(2, java.util.concurrent.TimeUnit.SECONDS).stream()
                        .map(TopWords.Count::token)
                        .collect(java.util.stream.Collectors.toSet());
                var secondTokens = second.get(2, java.util.concurrent.TimeUnit.SECONDS).stream()
                        .map(TopWords.Count::token)
                        .collect(java.util.stream.Collectors.toSet());
                assertEquals(expected("first"), firstTokens);
                assertEquals(expected("second"), secondTokens);
            }
        }
    }

    private static String tokens(String prefix) {
        return IntStream.range(0, 128)
                .mapToObj(index -> prefix + index)
                .collect(java.util.stream.Collectors.joining(" "));
    }

    private static Set<String> expected(String prefix) {
        var result = new HashSet<String>();
        for (int index = 0; index < 128; index++) result.add(prefix + index);
        return result;
    }
}
