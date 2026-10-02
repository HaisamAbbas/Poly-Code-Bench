package demo;

/**
 * Build-time offline self-check fixture.
 *
 * <p>Exists only so {@code mvn -o test} has something to compile and run inside the pinned image
 * with no network. It is never part of a scored run and no task references it.
 */
public final class SelfCheck {

    private SelfCheck() {
    }

    public static int one() {
        return 1;
    }
}