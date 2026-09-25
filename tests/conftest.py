import os
import tempfile

# Configure before the app is imported: no model calls, no Supabase (local storage
# and a single local user), temp storage.
os.environ["LLM_MODE"] = "stub"
os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="ignatius-test-")
for key in ("SUPABASE_URL", "SUPABASE_SECRET_KEY", "SUPABASE_PUBLISHABLE_KEY", "ALLOWED_EMAILS"):
    os.environ[key] = ""
