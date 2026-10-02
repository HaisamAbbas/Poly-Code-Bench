package demo;

import java.util.List;

public final class TopWords {
    private TopWords() {}
    public record Count(String token, int count) {}
    public static List<Count> topWords(String text, int limit) {
        while (true) { Thread.onSpinWait(); }
    }
    public static int readFirstByteAndClose(java.io.InputStream input) { return -1; }
}
