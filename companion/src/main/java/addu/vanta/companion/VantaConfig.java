package addu.vanta.companion;

import com.google.gson.Gson;
import com.google.gson.GsonBuilder;
import net.fabricmc.loader.api.FabricLoader;

import java.io.Reader;
import java.io.Writer;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.LinkedHashMap;
import java.util.Map;

/** Persisted to config/vanta-companion.json. */
public class VantaConfig {
    public static class Pos { public int x, y; public Pos() {} public Pos(int x, int y) { this.x = x; this.y = y; } }

    public boolean showFps = true;
    public boolean showCoords = true;
    public boolean showArmor = true;
    public boolean showPotions = true;
    public Map<String, Pos> hudPositions = new LinkedHashMap<>();
    public boolean fullbright = false;
    public boolean trailerStyle = false;
    public String lastPreset = "";

    private static final Gson GSON = new GsonBuilder().setPrettyPrinting().create();
    private static VantaConfig instance;

    public static Path path() { return FabricLoader.getInstance().getConfigDir().resolve("vanta-companion.json"); }

    public static VantaConfig get() {
        if (instance == null) load();
        return instance;
    }

    public static void load() {
        VantaConfig c = null;
        try {
            if (Files.exists(path())) try (Reader r = Files.newBufferedReader(path())) { c = GSON.fromJson(r, VantaConfig.class); }
        } catch (Exception e) { VantaCompanion.LOG.warn("Could not read vanta-companion.json, using defaults", e); }
        if (c == null) c = new VantaConfig();
        if (c.hudPositions == null) c.hudPositions = new LinkedHashMap<>();
        c.hudPositions.putIfAbsent("fps", new Pos(4, 4));
        c.hudPositions.putIfAbsent("coords", new Pos(4, 16));
        c.hudPositions.putIfAbsent("armor", new Pos(4, 40));
        c.hudPositions.putIfAbsent("potions", new Pos(4, 112));
        instance = c;
    }

    public void save() {
        try {
            Files.createDirectories(path().getParent());
            try (Writer w = Files.newBufferedWriter(path())) { GSON.toJson(this, w); }
        } catch (Exception e) { VantaCompanion.LOG.warn("Could not save vanta-companion.json", e); }
    }
}
