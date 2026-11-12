package io.github.sbrerup.sharecures;

import com.destroystokyo.paper.entity.villager.Reputation;
import com.destroystokyo.paper.entity.villager.ReputationType;
import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.HashSet;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;
import java.util.UUID;
import java.util.logging.Level;
import net.kyori.adventure.text.Component;
import net.kyori.adventure.text.format.NamedTextColor;
import org.bukkit.NamespacedKey;
import org.bukkit.command.Command;
import org.bukkit.command.CommandSender;
import org.bukkit.entity.Entity;
import org.bukkit.entity.Player;
import org.bukkit.entity.Villager;
import org.bukkit.entity.ZombieVillager;
import org.bukkit.event.EventHandler;
import org.bukkit.event.EventPriority;
import org.bukkit.event.Listener;
import org.bukkit.event.entity.EntityTransformEvent;
import org.bukkit.event.player.PlayerInteractEntityEvent;
import org.bukkit.persistence.PersistentDataType;
import org.bukkit.plugin.java.JavaPlugin;

/**
 * Lets a player share the discount they get for curing a zombie villager.
 *
 * <p>Prices stay vanilla. Curing gives the curer a permanent "major positive"
 * reputation on that villager, which vanilla never passes on. When someone opens
 * a trade, this plugin lends them the highest such reputation that a sharing
 * player has earned on that villager, and takes it back if that player has
 * stopped sharing since.
 */
public final class ShareCuresPlugin extends JavaPlugin implements Listener {
    private final Set<UUID> sharers = new HashSet<>();
    private NamespacedKey grantsKey;
    private Path sharersFile;

    @Override
    public void onEnable() {
        grantsKey = new NamespacedKey(this, "grants");
        sharersFile = getDataFolder().toPath().resolve("sharers.txt");
        loadSharers();
        getServer().getPluginManager().registerEvents(this, this);
    }

    /** Runs before the trade opens, which is when vanilla works out the prices. */
    @EventHandler(priority = EventPriority.HIGH, ignoreCancelled = true)
    public void onInteract(PlayerInteractEntityEvent event) {
        if (!(event.getRightClicked() instanceof Villager villager)) {
            return;
        }
        UUID trader = event.getPlayer().getUniqueId();
        Map<UUID, Integer> grants = readGrants(villager);

        int bestShared = 0;
        for (Map.Entry<UUID, Reputation> entry : villager.getReputations().entrySet()) {
            UUID player = entry.getKey();
            if (player.equals(trader) || !sharers.contains(player)) {
                continue;
            }
            int reputation = entry.getValue().getReputation(ReputationType.MAJOR_POSITIVE);
            bestShared = Math.max(bestShared, Grants.earned(reputation, grants.getOrDefault(player, 0)));
        }

        Reputation own = villager.getReputation(trader);
        int current = own == null ? 0 : own.getReputation(ReputationType.MAJOR_POSITIVE);
        int granted = grants.getOrDefault(trader, 0);
        Grants.Settled settled = Grants.settle(current, granted, bestShared);

        if (settled.reputation() != current) {
            if (own == null) {
                own = new Reputation();
            }
            own.setReputation(ReputationType.MAJOR_POSITIVE, settled.reputation());
            villager.setReputation(trader, own);
        }
        if (settled.granted() != granted) {
            if (settled.granted() > 0) {
                grants.put(trader, settled.granted());
            } else {
                grants.remove(trader);
            }
            writeGrants(villager, grants);
        }
    }

    /**
     * A villager that is zombified and cured becomes a new entity each time. Its
     * reputations are carried over by vanilla, so the record of loans has to be
     * carried over too, or every loan would turn into an earned discount.
     */
    @EventHandler(priority = EventPriority.MONITOR, ignoreCancelled = true)
    public void onTransform(EntityTransformEvent event) {
        boolean cured = event.getTransformReason() == EntityTransformEvent.TransformReason.CURED;
        if (!cured && event.getTransformReason() != EntityTransformEvent.TransformReason.INFECTION) {
            return;
        }
        Entity before = event.getEntity();
        Map<UUID, Integer> grants = readGrants(before);

        if (cured && before instanceof ZombieVillager zombie && zombie.getConversionPlayer() != null) {
            // Vanilla only rewards a curer who is in the same world when the cure finishes.
            Player curer = zombie.getConversionPlayer().getPlayer();
            if (curer != null && curer.getWorld().equals(before.getWorld())) {
                // From here on their discount is earned, not lent.
                grants.remove(curer.getUniqueId());
                if (!sharers.contains(curer.getUniqueId())) {
                    curer.sendMessage(Component.text(
                            "You cured a villager. Run /sharecures on to let everyone trade at your cure prices.",
                            NamedTextColor.GRAY));
                }
            }
        }
        writeGrants(event.getTransformedEntity(), grants);
    }

    @Override
    public boolean onCommand(CommandSender sender, Command command, String label, String[] args) {
        if (!(sender instanceof Player player)) {
            sender.sendMessage(Component.text("Only players can share cure discounts."));
            return true;
        }
        UUID id = player.getUniqueId();
        String choice = args.length == 0 ? "" : args[0].toLowerCase(Locale.ROOT);
        switch (choice) {
            case "on" -> {
                if (sharers.add(id)) {
                    saveSharers();
                }
                player.sendMessage(Component.text(
                        "Everyone now trades at your cure prices with villagers you have cured.",
                        NamedTextColor.GREEN));
            }
            case "off" -> {
                if (sharers.remove(id)) {
                    saveSharers();
                }
                player.sendMessage(Component.text(
                        "Your cure discounts are yours alone again. Others lose them the next time they trade.",
                        NamedTextColor.YELLOW));
            }
            case "" -> player.sendMessage(Component.text(
                    sharers.contains(id)
                            ? "You are sharing your cure discounts. Run /sharecures off to stop."
                            : "You are not sharing your cure discounts. Run /sharecures on to share them.",
                    NamedTextColor.GRAY));
            default -> {
                return false;
            }
        }
        return true;
    }

    @Override
    public List<String> onTabComplete(CommandSender sender, Command command, String alias, String[] args) {
        if (args.length != 1) {
            return List.of();
        }
        String typed = args[0].toLowerCase(Locale.ROOT);
        return List.of("on", "off").stream().filter(option -> option.startsWith(typed)).toList();
    }

    private Map<UUID, Integer> readGrants(Entity entity) {
        return Grants.parse(entity.getPersistentDataContainer().get(grantsKey, PersistentDataType.STRING));
    }

    private void writeGrants(Entity entity, Map<UUID, Integer> grants) {
        String text = Grants.format(grants);
        if (text.isEmpty()) {
            entity.getPersistentDataContainer().remove(grantsKey);
        } else {
            entity.getPersistentDataContainer().set(grantsKey, PersistentDataType.STRING, text);
        }
    }

    private void loadSharers() {
        if (!Files.exists(sharersFile)) {
            return;
        }
        try {
            for (String line : Files.readAllLines(sharersFile)) {
                if (!line.isBlank()) {
                    sharers.add(UUID.fromString(line.trim()));
                }
            }
        } catch (IOException | IllegalArgumentException e) {
            getLogger().log(Level.SEVERE, "Could not read " + sharersFile + ", so nobody is sharing", e);
        }
    }

    private void saveSharers() {
        try {
            Files.createDirectories(sharersFile.getParent());
            Path temporary = sharersFile.resolveSibling("sharers.txt.tmp");
            Files.write(temporary, sharers.stream().map(UUID::toString).sorted().toList());
            Files.move(temporary, sharersFile, java.nio.file.StandardCopyOption.REPLACE_EXISTING);
        } catch (IOException e) {
            getLogger().log(Level.SEVERE, "Could not save " + sharersFile, e);
        }
    }
}
