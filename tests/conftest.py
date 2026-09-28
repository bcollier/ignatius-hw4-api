import os
import tempfile

# Configure before the app is imported: no model calls, no Supabase (local storage
# and a single local user), temp storage.
os.environ["LLM_MODE"] = "stub"
os.environ["LOCAL_MODE"] = "1"  # one local user, no Supabase
os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="ignatius-test-")
for key in ("SUPABASE_URL", "SUPABASE_SECRET_KEY", "SUPABASE_PUBLISHABLE_KEY", "ALLOWED_EMAILS", "OPENROUTER_API_KEY", "ELEVENLABS_API_KEY", "JETSTREAM_API_KEY", "TAVILY_API_KEY", "EXA_API_KEY", "BRAVE_SEARCH_API_KEY", "BRAVE_ANSWERS_API_KEY", "FIRECRAWL_API_KEY", "LINKUP_API_KEY", "OPENAI_API_KEY", "XAI_API_KEY"):
    os.environ[key] = ""
