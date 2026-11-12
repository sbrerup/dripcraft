package io.github.sbrerup.sharecures;

import java.util.HashMap;
import java.util.Map;
import java.util.TreeMap;
import java.util.UUID;

/**
 * The cure reputation this plugin has lent to players on one villager, kept apart
 * from the reputation they earned by curing it themselves, so that a loan can be
 * taken back when the curer stops sharing.
 */
final class Grants {
    private Grants() {
    }

    /** What a player's cure reputation should be, and how much of it is lent. */
    record Settled(int reputation, int granted) {
    }

    /** The part of a player's cure reputation that they earned themselves. */
    static int earned(int reputation, int granted) {
        return Math.max(0, reputation - granted);
    }

    /**
     * @param reputation the player's cure reputation on the villager now
     * @param granted how much of that this plugin lent earlier
     * @param bestShared the highest earned cure reputation among sharing players
     */
    static Settled settle(int reputation, int granted, int bestShared) {
        int earned = earned(reputation, granted);
        int wanted = Math.max(earned, bestShared);
        return new Settled(wanted, wanted - earned);
    }

    /** Reads "uuid=amount;uuid=amount". Skips anything it cannot read. */
    static Map<UUID, Integer> parse(String text) {
        Map<UUID, Integer> grants = new HashMap<>();
        if (text == null) {
            return grants;
        }
        for (String entry : text.split(";")) {
            String[] parts = entry.split("=");
            if (parts.length != 2) {
                continue;
            }
            try {
                int amount = Integer.parseInt(parts[1]);
                if (amount > 0) {
                    grants.put(UUID.fromString(parts[0]), amount);
                }
            } catch (IllegalArgumentException ignored) {
                // A damaged entry only loses track of one loan.
            }
        }
        return grants;
    }

    static String format(Map<UUID, Integer> grants) {
        StringBuilder text = new StringBuilder();
        new TreeMap<>(grants).forEach((player, amount) -> {
            if (amount > 0) {
                if (!text.isEmpty()) {
                    text.append(';');
                }
                text.append(player).append('=').append(amount);
            }
        });
        return text.toString();
    }
}
