package demo;

import java.io.FileInputStream;
import java.io.IOException;
import java.util.List;

public final class TopWords {
    private TopWords() {}
    public record Count(String token, int count) {}
    public static List<Count> topWords(String text, int limit) { return List.of(); }
    public static int readFirstByteAndClose(java.io.InputStream input) { return -1; }
    public static int leakingRead(String path) throws IOException {
        FileInputStream input = new FileInputStream(path);
        return input.read();
    }
}
