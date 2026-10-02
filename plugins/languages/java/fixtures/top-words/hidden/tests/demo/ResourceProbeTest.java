package demo;

import static org.junit.jupiter.api.Assertions.assertTrue;

import java.io.ByteArrayInputStream;
import java.util.concurrent.atomic.AtomicBoolean;
import org.junit.jupiter.api.Test;

class ResourceProbeTest {
    @Test
    void closesOwnedInput() {
        var closed = new AtomicBoolean();
        var input = new ByteArrayInputStream(new byte[] {1}) {
            @Override
            public void close() {
                closed.set(true);
            }
        };
        TopWords.readFirstByteAndClose(input);
        assertTrue(closed.get(), "the method must close the input stream it owns");
    }
}
