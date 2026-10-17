import pytest

from vanta.launcher.minecraft.monitor import summarize_crash

CASES = [
    ("java.lang.OutOfMemoryError: Java heap space", "ran out of memory"),
    ("java.io.IOException: No space left on device", "disk is full"),
    ("Found 2 duplicate mods:\n\t- sodium", "installed twice"),
    ("Mod 'Iris' (iris) 1.8.8 requires any version of fabric-api, which is missing!", "Fabric API"),
    ("net.fabricmc.loader.impl.FormattedException: optifabric failed", "OptiFine"),
    ("Found a mods.toml in jee.jar: this is a Forge mod", "Forge"),
    ("Mod resolution failed", "missing a dependency"),
    ("java.lang.UnsupportedClassVersionError: class file version 65.0", "newer Java"),
    ("java.lang.reflect.InaccessibleObjectException: Unable to make field accessible: module java.base does not \"opens java.lang\"", "too new"),
    ("[Render thread/ERROR]: Failed to link program: composite1", "shader pack"),
    ("# EXCEPTION_ACCESS_VIOLATION (0xc0000005)\n# Problematic frame:\n# C  [nvoglv64.dll+0xd5c4a2]", "graphics driver crashed"),
    ("*** NSInternalInconsistencyException: NSWindow should only be instantiated on the main thread", "macOS"),
    ("org.spongepowered.asm.mixin.transformer.throwables.MixinApplyError", "Mixin"),
    ("java.lang.NoSuchMethodError: 'void net.minecraft.class_310.method_1507()'", "different Minecraft version"),
    ("com.google.gson.JsonSyntaxException: Expected BEGIN_OBJECT", "config file is damaged"),
    ("java.io.IOException: Exception reading r.0.0.mca", "world file looks damaged"),
    ("Description: Ticking entity\n\njava.lang.NullPointerException", "Something in the world"),
    ("java.lang.StackOverflowError", "loop"),
    ("java.util.zip.ZipException: zip END header not found", "half-finished download"),
    ("Error: Could not find or load main class net.minecraft.client.main.Main", "incomplete"),
    ("java.nio.file.AccessDeniedException: C:\\Users\\a\\.minecraft\\options.txt", "antivirus"),
    ("java.net.BindException: Address already in use", "network port"),
]


@pytest.mark.parametrize("text,expect", CASES)
def test_crash_rules(text, expect):
    report = text if text.startswith("Description") else ""
    r = summarize_crash(text.splitlines(), report)
    assert expect.lower() in r["hint"].lower(), (text, r["hint"])


def test_unknown_crash_keeps_generic_hint():
    assert "Open the log" in summarize_crash(["something odd happened"])["hint"]
