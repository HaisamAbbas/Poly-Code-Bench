package demo;

import static org.junit.jupiter.api.Assertions.assertTrue;

import java.io.ByteArrayInputStream;
import org.junit.jupiter.api.Test;

class ResourceProbeTest {
    @Test
    void closesOwnedInput() {
        var input = new ByteArrayInputStream(new byte[] {1});
        TopWords.readFirstByteAndClose(input);
        assertTrue(input.available() == 0);
    }
}
