import os

# Make provider resolution deterministic regardless of the developer's shell.
for var in (
    "GROQ_API_KEY",
    "CEREBRAS_API_KEY",
    "OPENROUTER_API_KEY",
    "TOGETHER_API_KEY",
    "HF_TOKEN",
    "OLLAMA_ENABLED",
):
    os.environ.pop(var, None)
