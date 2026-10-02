package demo;

import java.io.IOException;
import java.util.List;

public final class TopWords {
    private TopWords() {}
    public record Count(String token, int count) {}
    public static List<Count> topWords(String text, int limit) { return List.of(); }
    public static int readFirstByteAndClose(java.io.InputStream input) { return -1; }
    public static Process runTool(String userValue) throws IOException {
        return Runtime.getRuntime().exec("lookup " + userValue);
    }
}
