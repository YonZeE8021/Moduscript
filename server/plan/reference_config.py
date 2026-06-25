"""Token and size limits for plan reference materialization."""

from config import DATA_DIR

CARD_MAX_CHARS = 8000
FINDINGS_SUMMARY_MAX_CHARS = 6000
FINDING_PREVIEW_MAX_CHARS = 400
SNIPPET_MAX_LINES = 150
SNIPPET_MAX_BYTES = 32_000
LOOKUPS_PER_TURN = 1
SNIPPETS_PER_LOOKUP = 3
READ_TOOL_MAX_ROUNDS = 15
READ_TOOL_MAX_BYTES_PER_READ = 64_000
READ_TOOL_MAX_TOTAL_BYTES = 400_000
READ_TOOL_LIST_MAX_ENTRIES = 100
READ_CONTEXT_MAX_CHARS = 8000
READABLE_SOURCE_KINDS = frozenset({"git", "decompiled", "decompiled_obfuscated"})
MAX_CODE_REFS = 3
MAX_SEARCH_HITS = 20
CLONE_TIMEOUT_SEC = 120
DECOMPILE_TIMEOUT_SEC = 300
MATERIALIZE_TIMEOUT_SEC = 600
READ_LOOP_TIMEOUT_SEC = 180
MAX_ARTIFACT_MB = 80
INDEX_MAX_FILES = 5000
ENTRY_POINT_MAX = 20

TOOLS_DIR = DATA_DIR / "tools"
VINEFLOWER_VERSION = "1.11.1"
TINY_REMAPPER_VERSION = "0.10.4"
VINEFLOWER_URL = (
    f"https://github.com/Vineflower/vineflower/releases/download/"
    f"{VINEFLOWER_VERSION}/vineflower-{VINEFLOWER_VERSION}.jar"
)
TINY_REMAPPER_URL = (
    f"https://maven.fabricmc.net/net/fabricmc/tiny-remapper/"
    f"{TINY_REMAPPER_VERSION}/tiny-remapper-{TINY_REMAPPER_VERSION}-fat.jar"
)
TINY_REMAPPER_MIN_BYTES = 500_000
VINEFLOWER_JAR = TOOLS_DIR / "vineflower.jar"
TINY_REMAPPER_JAR = TOOLS_DIR / "tiny-remapper.jar"

SKIP_DIR_NAMES = frozenset({".git", "build", "out", "bin", "node_modules", ".gradle", "run"})
KEY_FILENAMES = frozenset(
    {
        "fabric.mod.json",
        "mods.toml",
        "gradle.properties",
        "settings.gradle",
        "build.gradle",
    }
)
