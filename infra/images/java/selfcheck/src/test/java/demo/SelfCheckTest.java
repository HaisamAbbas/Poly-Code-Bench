package demo;

import static org.junit.jupiter.api.Assertions.assertEquals;

import org.junit.jupiter.api.Test;

/**
 * Build-time offline self-check test. See {@link SelfCheck}: if this runs, the image's seeded
 * repository carried JUnit, surefire and the compiler plugin, all resolved with {@code --offline}.
 */
class SelfCheckTest {

    @Test
    void compilesAndRunsOffline() {
        assertEquals(1, SelfCheck.one());
    }
}