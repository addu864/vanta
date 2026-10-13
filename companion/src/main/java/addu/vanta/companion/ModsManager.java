package addu.vanta.companion;

import net.fabricmc.loader.api.FabricLoader;
import net.fabricmc.loader.api.ModContainer;
import net.minecraft.util.Util;

import java.io.IOException;
import java.nio.file.*;
import java.util.*;

/** Lists mods and flips them between .jar and .jar.disabled for the next launch. */
public final class ModsManager {
    public static final class Entry {
        public final String id, name, version; public final Path file; public boolean enabledNext; public final boolean loadedNow;
        Entry(String id, String name, String version, Path file, boolean enabledNext, boolean loadedNow) {
            this.id = id; this.name = name; this.version = version; this.file = file; this.enabledNext = enabledNext; this.loadedNow = loadedNow;
        }
        public boolean pending() { return enabledNext != loadedNow; }
    }

    /** Renames that failed because the jar is locked (Windows); applied by a helper after the game exits. */
    private static final Map<Path, Path> deferred = new LinkedHashMap<>();
    private static boolean hookInstalled;

    public static Path modsDir() { return FabricLoader.getInstance().getGameDir().resolve("mods"); }

    public static List<Entry> list() {
        List<Entry> out = new ArrayList<>();
        Set<Path> seen = new HashSet<>();
        for (ModContainer m : FabricLoader.getInstance().getAllMods()) {
            if (m.getContainingMod().isPresent()) continue;
            String id = m.getMetadata().getId();
            if (id.equals("minecraft") || id.equals("java") || id.equals("fabricloader") || m.getMetadata().getType().equals("builtin")) continue;
            Path file = null;
            try {
                for (Path p : m.getOrigin().getPaths()) if (p.toString().endsWith(".jar")) { file = p.toAbsolutePath(); break; }
            } catch (Throwable ignored) {}
            boolean enabledNext = true;
            if (file != null) {
                seen.add(file);
                Path dis = file.resolveSibling(file.getFileName() + ".disabled");
                if (!Files.exists(file) && Files.exists(dis)) enabledNext = false;
                if (deferred.containsKey(file)) enabledNext = false;
            }
            out.add(new Entry(id, m.getMetadata().getName(), m.getMetadata().getVersion().getFriendlyString(), file, enabledNext, true));
        }
        try (DirectoryStream<Path> ds = Files.newDirectoryStream(modsDir())) {
            for (Path p : ds) {
                String n = p.getFileName().toString();
                if (n.endsWith(".jar.disabled")) {
                    Path jar = p.resolveSibling(n.substring(0, n.length() - ".disabled".length())).toAbsolutePath();
                    if (seen.contains(jar)) continue;
                    out.add(new Entry(n, n.substring(0, n.length() - ".jar.disabled".length()), "disabled", jar, false, false));
                } else if (n.endsWith(".jar") && !seen.contains(p.toAbsolutePath())) {
                    // jar re-enabled this session (not loaded yet)
                    out.add(new Entry(n, n.substring(0, n.length() - 4), "enabled on restart", p.toAbsolutePath(), true, false));
                }
            }
        } catch (IOException ignored) {}
        out.sort(Comparator.comparing(e -> e.name.toLowerCase(Locale.ROOT)));
        return out;
    }

    /** @return null on success, otherwise a short reason. */
    public static String setEnabled(Entry e, boolean enable) {
        if (e.file == null) return "not a standalone jar";
        if (e.id.equals(VantaCompanion.MOD_ID) && !enable) return "can't disable itself here";
        Path jar = e.file, dis = jar.resolveSibling(jar.getFileName() + ".disabled");
        Path from = enable ? dis : jar, to = enable ? jar : dis;
        if (enable && deferred.remove(jar) != null) { e.enabledNext = true; return null; }
        if (!Files.exists(from)) return "file missing";
        try {
            Files.move(from, to);
            e.enabledNext = enable;
            return null;
        } catch (IOException ex) {
            if (!enable) { // locked jar on Windows: do it once the JVM has released it
                deferred.put(jar, dis);
                installHook();
                e.enabledNext = false;
                return null;
            }
            return "rename failed: " + ex.getMessage();
        }
    }

    private static synchronized void installHook() {
        if (hookInstalled) return;
        hookInstalled = true;
        Runtime.getRuntime().addShutdownHook(new Thread(() -> {
            if (deferred.isEmpty()) return;
            try {
                StringBuilder cmd = new StringBuilder("ping -n 4 127.0.0.1 >nul");
                for (var en : deferred.entrySet())
                    cmd.append(" & move /Y \"").append(en.getKey()).append("\" \"").append(en.getValue()).append("\"");
                if (Util.getOperatingSystem() == Util.OperatingSystem.WINDOWS)
                    new ProcessBuilder("cmd", "/c", cmd.toString()).start();
                else for (var en : deferred.entrySet()) Files.move(en.getKey(), en.getValue(), StandardCopyOption.REPLACE_EXISTING);
            } catch (Exception ex) { VantaCompanion.LOG.error("Deferred mod rename failed", ex); }
        }, "vanta-companion-mod-rename"));
    }
}
