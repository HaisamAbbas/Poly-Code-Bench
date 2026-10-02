package demo;

import static org.junit.jupiter.api.Assertions.assertEquals;

import java.util.List;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

class TopWordsTest {
    @Test
    @DisplayName("count order has deterministic lexical ties")
    void ordersByCountAndToken() {
        assertEquals(List.of(new TopWords.Count("a", 2), new TopWords.Count("b", 2)),
                TopWords.topWords("b a a b c", 2));
    }

    @Test
    void handlesNullAndNonPositiveLimit() {
        assertEquals(List.of(), TopWords.topWords(null, 2));
        assertEquals(List.of(), TopWords.topWords("a", 0));
    }
}
