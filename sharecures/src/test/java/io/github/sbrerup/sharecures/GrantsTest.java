package io.github.sbrerup.sharecures;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.util.Map;
import java.util.UUID;
import org.junit.jupiter.api.Test;

class GrantsTest {
    private static final UUID A = UUID.fromString("00000000-0000-0000-0000-00000000000a");
    private static final UUID B = UUID.fromString("00000000-0000-0000-0000-00000000000b");

    @Test
    void lendsTheSharedDiscountToAPlayerWithNone() {
        assertEquals(new Grants.Settled(20, 20), Grants.settle(0, 0, 20));
    }

    @Test
    void leavesAPlayerAloneWhenNobodyShares() {
        assertEquals(new Grants.Settled(0, 0), Grants.settle(0, 0, 0));
    }

    @Test
    void takesTheLoanBackWhenTheCurerStopsSharing() {
        assertEquals(new Grants.Settled(0, 0), Grants.settle(20, 20, 0));
    }

    @Test
    void keepsWhatThePlayerEarnedWhenTheCurerStopsSharing() {
        assertEquals(new Grants.Settled(20, 0), Grants.settle(20, 0, 0));
    }

    @Test
    void doesNotLendOnTopOfAnEarnedDiscount() {
        assertEquals(new Grants.Settled(20, 0), Grants.settle(20, 0, 20));
    }

    @Test
    void isStableWhenRunTwice() {
        Grants.Settled first = Grants.settle(0, 0, 20);
        assertEquals(first, Grants.settle(first.reputation(), first.granted(), 20));
    }

    @Test
    void aLoanIsNotCountedAsEarned() {
        assertEquals(0, Grants.earned(20, 20));
        assertEquals(20, Grants.earned(20, 0));
        assertEquals(0, Grants.earned(5, 20));
    }

    @Test
    void grantsSurviveBeingWrittenAndRead() {
        Map<UUID, Integer> grants = Map.of(A, 20, B, 5);
        assertEquals(grants, Grants.parse(Grants.format(grants)));
    }

    @Test
    void emptyAndDamagedTextReadAsNoGrants() {
        assertTrue(Grants.parse(null).isEmpty());
        assertTrue(Grants.parse("").isEmpty());
        assertEquals(Map.of(A, 20), Grants.parse("junk;" + A + "=20;" + B + "=x;=;" + B + "=0"));
    }
}
