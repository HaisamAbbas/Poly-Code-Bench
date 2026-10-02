package demo;

import static org.junit.jupiter.api.Assertions.assertEquals;

import java.util.concurrent.Executors;
import org.junit.jupiter.api.Test;

class ConcurrencyProbeTest {
    @Test
    void parallelCallsRemainIndependent() throws Exception {
        try (var executor = Executors.newFixedThreadPool(4)) {
            var first = executor.submit(() -> TopWords.topWords("x x y", 2));
            var second = executor.submit(() -> TopWords.topWords("y y x", 2));
            assertEquals("x", first.get().getFirst().token());
            assertEquals("y", second.get().getFirst().token());
        }
    }
}
